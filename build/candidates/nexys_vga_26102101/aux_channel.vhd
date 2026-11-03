----------------------------------------------------------------------------------
-- Engineer: Mike Field <hamster@snap.net.nz<
--
-- Module Name: aux_channel - Behavioral
--
-- Description: A more usable interface for sending/receiving data over the
--              DisplayPort AUX channel. It also implements the timeout.
--
------------------------------------------------------------------------------------
-- FPGA_DisplayPort from https://github.com/hamsternz/FPGA_DisplayPort
------------------------------------------------------------------------------------
-- The MIT License (MIT)
--
-- Copyright (c) 2015 Michael Alan Field <hamster@snap.net.nz>
--
-- Permission is hereby granted, free of charge, to any person obtaining a copy
-- of this software and associated documentation files (the "Software"), to deal
-- in the Software without restriction, including without limitation the rights
-- to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
-- copies of the Software, and to permit persons to whom the Software is
-- furnished to do so, subject to the following conditions:
--
-- The above copyright notice and this permission notice shall be included in
-- all copies or substantial portions of the Software.
--
-- THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
-- IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
-- FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
-- AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
-- LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
-- OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
-- THE SOFTWARE.
------------------------------------------------------------------------------------
----- Want to say thanks? ----------------------------------------------------------
------------------------------------------------------------------------------------
--
-- This design has taken many hours - 3 months of work. I'm more than happy
-- to share it if you can make use of it. It is released under the MIT license,
-- so you are not under any onus to say thanks, but....
--
-- If you what to say thanks for this design either drop me an email, or how about
-- trying PayPal to my email (hamster@snap.net.nz)?
--
--  Educational use - Enough for a beer
--  Hobbyist use    - Enough for a pizza
--  Research use    - Enough to take the family out to dinner
--  Commercial use  - A weeks pay for an engineer (I wish!)
--------------------------------------------------------------------------------------
--  Ver | Date       | Change
--------+------------+---------------------------------------------------------------
--  0.1 | 2015-09-17 | Initial Version
------------------------------------------------------------------------------------

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity aux_channel is
        generic(enable_link_probe : boolean := false;
                link_poll_cycles : positive := 100000000;
                enable_ser : boolean := false; ser_cycles_per_ms : positive := 100000;
                disable_scrambling : boolean := false;
                initialize_sink_power : boolean := false);
        port (
           clk                 : in    std_logic;
           debug_pmod          : out   std_logic_vector(7 downto 0);
           diagnostic_status   : out std_logic_vector(47 downto 0) := (others=>'0');
           probe_errors, probe_time, probe_config, probe_metadata : out std_logic_vector(63 downto 0) := (others=>'0');
           ser_arm : out std_logic := '0';
           ser_stream_ready : in std_logic := '0';
           ser_diagnostic : out std_logic_vector(383 downto 0) := (others=>'0');
           sink_power_status : out std_logic_vector(63 downto 0) := (others=>'0');
           ------------------------------
           edid_de             : out   std_logic;
           dp_reg_de           : out   std_logic;
           adjust_de           : out   std_logic;
           status_de           : out   std_logic;
           aux_addr            : out   std_logic_vector(7 downto 0);
           aux_data            : out   std_logic_vector(7 downto 0);
           ------------------------------
           enhanced_framing : in std_logic := '0';
         link_count          : in    std_logic_vector(2 downto 0);
           ------------------------------
           hpd_irq             : in    std_logic;
           hpd_present         : in    std_logic;
           sink_capable        : in    std_logic := '1';
           tx_ready            : in    std_logic := '1';
           training_interval   : in std_logic_vector(6 downto 0) := (others=>'0');
           dpcd_revision       : in std_logic_vector(7 downto 0) := x"12";
           capabilities_reset  : out   std_logic := '1';
           ------------------------------
           tx_powerup          : out   std_logic := '0';
           tx_clock_train      : out   std_logic := '0';
           tx_align_train      : out   std_logic := '0';
           tx_link_established : out   std_logic := '0';
           -------------------------------
           swing_0p4           : in    std_logic;
           swing_0p6           : in    std_logic;
           swing_0p8           : in    std_logic;
           preemp_0p0          : in    STD_LOGIC;
           preemp_3p5          : in    STD_LOGIC;
           preemp_6p0          : in    STD_LOGIC;
           clock_locked        : in    STD_LOGIC;
           equ_locked          : in    STD_LOGIC;
           symbol_locked       : in    STD_LOGIC;
           align_locked        : in    STD_LOGIC;
           ------------------------------
           dp_tx_hp_detect     : in    std_logic;
           dp_tx_aux_p         : out std_logic;
           dp_tx_aux_n         : out std_logic;
           dp_rx_aux_p         : in std_logic;
           dp_rx_aux_n         : in std_logic
        );
end entity;

architecture arch of aux_channel is
    type t_state is ( error, reset, check_presence,
                    -- Gathering Display information
                    edid_block0, edid_block1, edid_block2, edid_block3,
                    edid_block4, edid_block5, edid_block6, edid_block7, edid_stop, edid_abort,
                    -- Gathering display Port information
                    read_sink_count, read_registers, validate_capabilities,
                    -- Link configuration states
                    set_channel_coding, set_speed_270, set_downspread, set_link_count_1, set_link_count_2, set_link_count_4, wait_gtp_ready,
                    -- Link training - clock recovery
                    clock_training, clock_voltage_0p4,  clock_voltage_0p6, clock_voltage_0p8,  clock_wait, clock_test, clock_adjust, clock_wait_after,
                    -- Link training - alignment and preemphasis
                    align_training,
                    align_p0_V0p4, align_p0_V0p6, align_p0_V0p8,
                    align_p1_V0p4, align_p1_V0p6, align_p1_V0p8,
                    align_p2_V0p4, align_p2_V0p6, align_p2_V0p8,
                    align_wait0,
                    align_test,    align_adjust,  align_wait_after,
                    -- Link up.
                    switch_to_normal, link_established,
                    --
                    check_link, check_wait, read_link_config, read_symbol_errors,
                    probe_config_done, probe_errors_done, normal_lane_settings, ser_prepare,
                    -- Appended to preserve existing telemetry state identities.
                    read_sink_power, set_sink_d0, wait_sink_d0, verify_sink_d0
                    );


    function failure_state(current : t_state) return t_state is
    begin
        -- Release MOT after a failed EDID transfer before retrying discovery.
        -- A failed STOP is terminal: never recursively try to abort an abort.
        if current=read_link_config then return probe_config_done;
        elsif current=read_symbol_errors then return probe_errors_done;
        elsif current>=check_presence and current<=edid_block7 then return edid_abort;
        else return error; end if;
    end;
    signal state            : t_state               := error;
    signal next_state       : t_state               := error;
    signal state_on_success : t_state               := error;
    signal last_lane_message : std_logic_vector(7 downto 0) := x"14";
    -- Local startup budget: 10 ms at the fixed 100 MHz management clock.
    signal ready_wait_cycles : natural range 0 to 999999 := 0;
    signal power_wait_cycles : natural range 0 to 99999 := 0;
    signal power_before, power_after, power_flags : std_logic_vector(7 downto 0) := (others=>'0');
    signal power_sequence : unsigned(15 downto 0) := (others=>'0');
    signal power_link_live : std_logic;
    signal training_wait_cycles : natural range 0 to 1599999 := 0;
    signal cr_attempts, eq_attempts : natural range 0 to 5 := 0;
    function training_delay(interval_code:std_logic_vector(6 downto 0);
                            revision:std_logic_vector(7 downto 0); is_cr:boolean) return natural is
        variable code:natural := to_integer(unsigned(interval_code));
    begin
        if is_cr and (code=0 or unsigned(revision)>=16#14#) then return 9999;
        elsif code=0 then return 39999;
        else
            -- Only four legal multipliers exist. Constant selection avoids
            -- synthesizing an unpipelined DSP for a slow configuration value.
            case code is
                when 1 => return 399999;
                when 2 => return 799999;
                when 3 => return 1199999;
                when others => return 1599999; -- 4; parent rejects reserved codes
            end case;
        end if;
    end;
    function lane_message(v : natural; p1,p2 : std_logic) return std_logic_vector is
        variable p : natural range 0 to 2 := 0;
    begin
        if p2='1' then p:=2; elsif p1='1' then p:=1; end if;
        if p>3-v then p:=3-v; end if;
        return std_logic_vector(to_unsigned(16#14# + 2*v + 16*p,8));
    end;
    signal retry_now : std_logic             := '0';
    signal retry_count        : unsigned(26 downto 0) := (9=>'1',others => '0');
    signal link_check_now : std_logic             := '0';
    signal link_check_count        : unsigned(26 downto 0) := (9=>'1',others => '0');
    signal count_100us      : unsigned(14 downto 0) := to_unsigned(1000,15);
    component dp_aux_messages is
    port ( clk          : in  std_logic;
           enhanced_framing : in std_logic := '0';
           -- Interface to send messages
           msg_de       : in  std_logic;
           msg          : in  std_logic_vector(7 downto 0);
           busy         : out std_logic;
           --- Interface to the AUX Channel
           aux_tx_wr_en : out std_logic;
           aux_tx_data  : out std_logic_vector(7 downto 0)
         );
    end component;

    component aux_interface is
        port (
           clk          : in    std_logic;
           debug_pmod   : out   std_logic_vector(7 downto 0);
           ------------------------------
           dp_tx_aux_p  : out std_logic;
           dp_tx_aux_n  : out std_logic;
           dp_rx_aux_p  : in std_logic;
           dp_rx_aux_n  : in std_logic;
           ------------------------------
           tx_wr_en     : in    std_logic;
           tx_data      : in    std_logic_vector(7 downto 0);
           tx_full      : out   std_logic;
           ------------------------------
           rx_rd_en     : in    std_logic;
           rx_data      : out   std_logic_vector(7 downto 0);
           rx_empty     : out   std_logic;
           ------------------------------
           busy         : out   std_logic;
           timeout      : out   std_logic
         );
    end component;


    signal adjust_de_active : std_logic := '0';
    signal dp_reg_de_active : std_logic := '0';
    signal edid_de_active   : std_logic := '0';
    signal status_de_active : std_logic := '0';
    signal msg_de           : std_logic := '0';
    signal msg              : std_logic_vector(7 downto 0);
    signal msg_busy         : std_logic := '0';

    signal aux_tx_wr_en    : std_logic;
    signal aux_tx_data     : std_logic_vector(7 downto 0);

    signal aux_rx_rd_en    : std_logic;
    signal aux_rx_data     : std_logic_vector(7 downto 0);
    signal aux_rx_empty    : std_logic;

    signal link_count_sink : std_logic_vector(7 downto 0);

    signal channel_busy    : std_logic;
    signal channel_timeout : std_logic;

    signal expected             : unsigned(7 downto 0);
    signal rx_byte_count        : unsigned(7 downto 0) := (others => '0');
    signal aux_addr_i           : unsigned(7 downto 0)  := (others => '0');
    signal reset_addr_on_change : std_logic;

    signal just_read_from_rx :std_logic := '0';
    signal powerup_mask  : std_logic_vector(3 downto 0);
    signal debug_pmod_i  : std_logic_vector(7 downto 0);
    -- SR-LUT adaptation: use physical HPD, not failed EDID reads, for presence.
    signal hpd_was_present : std_logic := '0';
    signal irq_pending : std_logic := '0';
    -- Separate budgets allow seven retransmissions for each DEFER class.
    -- Partial write ACK consumes the I2C budget; alternating replies cannot
    -- keep discovery alive indefinitely (at most fourteen retransmissions).
    -- A deferred offset write is polled with WRITE_STATUS_UPDATE, retaining
    -- MOT; it must not be mistaken for a completed offset write.
    signal write_status_pending : std_logic := '0';
    signal reply_accepted : std_logic := '0';
    signal defer_retries : natural range 0 to 7 := 0;
    signal i2c_retries : natural range 0 to 7 := 0;
    signal defer_pending : std_logic := '0';
    signal defer_delay : natural range 0 to 39999 := 0;
    signal retry_launch : std_logic := '0';
    -- A reply has at most sixteen payload bytes. Do not expose a prefix
    -- to EDID/DPCD/training consumers until the complete packet is valid.
    type payload_buffer_type is array (0 to 15) of std_logic_vector(7 downto 0);
    signal payload_buffer : payload_buffer_type;
    signal commit_remaining : natural range 0 to 16 := 0;
    signal commit_index : natural range 0 to 15 := 0;
    signal last_failure, last_reply, last_rx : std_logic_vector(7 downto 0) := (others=>'0');
    signal failure_count : unsigned(7 downto 0) := (others=>'0');
    signal probe_complete : std_logic := '0';
    signal probe_reason : std_logic_vector(3 downto 0) := x"5";
    signal probe_ms_divider : natural range 0 to 99999 := 0;
    signal probe_ms, previous_error_ms : unsigned(31 downto 0) := (others=>'0');
    signal previous_error_valid : std_logic := '0';
    signal error_sequence, config_sequence : unsigned(15 downto 0) := (others=>'0');
    signal error_record, time_record, config_record, config_meta : std_logic_vector(63 downto 0) := (others=>'0');
    component ser_measurement_window is
        generic(cycles_per_ms : positive := 100000; window_ms : positive range 10000 to 99000 := 20000; timeout_ms : positive := 1000);
        port(clk : in std_logic; qualified,external_clear : in std_logic;
             request_valid : out std_logic; request_ready : in std_logic; request_tag : out unsigned(31 downto 0);
             cancel : out std_logic; reply_valid,reply_success : in std_logic;
             reply_tag : in unsigned(31 downto 0); reply_data : in std_logic_vector(31 downto 0);
             sample_valid,sample_success,interval_known : out std_logic;
             sample_raw : out std_logic_vector(31 downto 0); lane_valid,lane_saturated : out std_logic_vector(1 downto 0);
             interval_cycles,interval_min_cycles,interval_max_cycles : out unsigned(63 downto 0);
             reason : out unsigned(2 downto 0); epoch : out unsigned(31 downto 0));
    end component;
    signal ser_ready_meta,ser_ready_sync,ser_config_ok,ser_qualified : std_logic := '0';
    attribute ASYNC_REG : string;
    attribute SHREG_EXTRACT : string;
    attribute ASYNC_REG of ser_ready_meta,ser_ready_sync : signal is "TRUE";
    attribute SHREG_EXTRACT of ser_ready_meta,ser_ready_sync : signal is "NO";
    signal ser_request,ser_accept,ser_cancel,ser_reply,ser_success : std_logic := '0';
    signal ser_sample,ser_sample_success,ser_known : std_logic := '0';
    signal ser_tag,ser_accepted_tag,ser_reply_tag,ser_epoch : unsigned(31 downto 0) := (others=>'0');
    signal ser_reply_data,ser_raw : std_logic_vector(31 downto 0) := (others=>'0');
    signal ser_valid,ser_saturated : std_logic_vector(1 downto 0);
    signal ser_interval,ser_min,ser_max : unsigned(63 downto 0);
    signal ser_reason : unsigned(2 downto 0);
    signal ser_sequence : unsigned(15 downto 0) := (others=>'0');
    signal ser_record : std_logic_vector(383 downto 0) := (others=>'0');
    signal ser_wait_cycles : natural range 0 to 9999999 := 0;
    signal bypass_config_ok : std_logic := '0';
    function lane_value(message_id:std_logic_vector(7 downto 0)) return std_logic_vector is
    begin
        case message_id is
            when x"14"=>return x"00"; when x"16"=>return x"01"; when x"18"=>return x"06";
            when x"24"=>return x"08"; when x"26"=>return x"09"; when x"28"|x"38"=>return x"2E";
            when x"34"=>return x"30"; when x"36"=>return x"35"; when others=>return x"FF";
        end case;
    end;
begin
    -- VGA candidate: bit4 zero-register quirk used; bit0 pre-read complete, bit1 D0 write ACK,
    -- bit2 post-read complete, bit3 post-read is D0. ACK alone never qualifies.
    power_link_live <= hpd_present and tx_ready when state=link_established or state=check_link or state=check_wait or
        state=read_link_config or state=read_symbol_errors or state=probe_config_done or state=probe_errors_done else '0';
    sink_power_status <= std_logic_vector(power_sequence) & dpcd_revision &
                         hpd_present & power_link_live & "0" & power_flags(4 downto 0) &
                         power_before & power_after & x"0000";
    process(clk) begin
        if rising_edge(clk) then
            if state/=wait_sink_d0 then power_wait_cycles<=0;
            elsif power_wait_cycles<99999 then power_wait_cycles<=power_wait_cycles+1; end if;
        end if;
    end process;
    assert not enable_ser or enable_link_probe report "SER requires diagnostic readback" severity failure;
    assert not disable_scrambling or (enable_link_probe and not enable_ser)
        report "Scrambler bypass requires separate normal-video readback" severity failure;
    assert not enable_ser or ser_cycles_per_ms<=100000 report "SER clock scale exceeds timestamp divider" severity failure;
    ser_diagnostic<=ser_record;
    process(clk)
    begin
        if rising_edge(clk) and disable_scrambling then
            if next_state/=state and state=read_link_config then
                bypass_config_ok<='0';
                if probe_complete='1' and enhanced_framing='1' and link_count="010"
                   and payload_buffer(0)=x"0A" and payload_buffer(1)=x"82" and payload_buffer(2)=x"20"
                   and payload_buffer(3)=lane_value(last_lane_message) and payload_buffer(4)=lane_value(last_lane_message)
                   and payload_buffer(5)=lane_value(last_lane_message) and payload_buffer(6)=lane_value(last_lane_message)
                   and payload_buffer(7)=x"00" and payload_buffer(8)=x"01" then bypass_config_ok<='1'; end if;
            end if;
            if hpd_present='0' or tx_ready='0' or next_state=error or next_state=reset then bypass_config_ok<='0'; end if;
        end if;
    end process;
    ser_accept<='1' when enable_ser and state=link_established and next_state=state
        and link_check_now='0' and irq_pending='0' and channel_busy='0' and msg_busy='0'
        and aux_rx_empty='1' and just_read_from_rx='0' else '0';
    ser_qualified<=ser_config_ok and ser_ready_sync and hpd_present and tx_ready
        when enable_ser and state/=error and state/=reset and next_state/=error and next_state/=reset else '0';
    g_ser_window: if enable_ser generate
        window_control: ser_measurement_window generic map(cycles_per_ms=>ser_cycles_per_ms)
            port map(clk=>clk,qualified=>ser_qualified,external_clear=>'0',request_valid=>ser_request,
                request_ready=>ser_accept,request_tag=>ser_tag,cancel=>ser_cancel,
                reply_valid=>ser_reply,reply_success=>ser_success,reply_tag=>ser_reply_tag,reply_data=>ser_reply_data,
                sample_valid=>ser_sample,sample_success=>ser_sample_success,interval_known=>ser_known,
                sample_raw=>ser_raw,lane_valid=>ser_valid,lane_saturated=>ser_saturated,
                interval_cycles=>ser_interval,interval_min_cycles=>ser_min,interval_max_cycles=>ser_max,
                reason=>ser_reason,epoch=>ser_epoch);
    end generate;
    process(clk)
        variable flags : std_logic_vector(7 downto 0);
    begin
        if rising_edge(clk) and enable_ser then
            ser_ready_meta<=ser_stream_ready; ser_ready_sync<=ser_ready_meta;
            ser_reply<='0';
            if ser_request='1' and ser_accept='1' then ser_accepted_tag<=ser_tag; end if;
            if next_state/=state and state=read_link_config then
                ser_config_ok<='0';
                if probe_complete='1' and dpcd_revision=x"11" and enhanced_framing='1' and link_count="010"
                   and payload_buffer(0)=x"0A" and payload_buffer(1)=x"82" and payload_buffer(2)=x"08"
                   and payload_buffer(3)=lane_value(last_lane_message) and payload_buffer(4)=lane_value(last_lane_message)
                   and payload_buffer(5)=lane_value(last_lane_message) and payload_buffer(6)=lane_value(last_lane_message)
                   and payload_buffer(7)=x"00" and payload_buffer(8)=x"01" then ser_config_ok<='1'; end if;
            end if;
            if next_state/=state and state=read_symbol_errors then
                ser_reply<='1'; ser_success<=probe_complete; ser_reply_tag<=ser_accepted_tag;
                ser_reply_data<=payload_buffer(3)&payload_buffer(2)&payload_buffer(1)&payload_buffer(0);
            end if;
            if ser_sample='1' then
                ser_sequence<=ser_sequence+1;
                flags:='1' & ser_qualified & ser_saturated & ser_valid & ser_known & ser_sample_success;
                ser_record(63 downto 0)<=std_logic_vector(ser_epoch)&std_logic_vector(ser_sequence+1)&flags&x"53";
                ser_record(127 downto 64)<=std_logic_vector(ser_tag)&ser_raw;
                ser_record(191 downto 128)<=std_logic_vector(ser_interval);
                ser_record(255 downto 192)<=std_logic_vector(ser_min);
                ser_record(319 downto 256)<=std_logic_vector(ser_max);
                ser_record(383 downto 320)<=std_logic_vector(config_sequence)&dpcd_revision&
                    tx_ready&hpd_present&ser_config_ok&ser_ready_sync&ser_qualified&std_logic_vector(ser_reason)&std_logic_vector(probe_ms);
            end if;
            if hpd_present='0' or tx_ready='0' or ser_ready_sync='0' or next_state=error or next_state=reset then
                ser_config_ok<='0';
            end if;
            if state/=ser_prepare then ser_wait_cycles<=0;
            elsif ser_wait_cycles<9999999 then ser_wait_cycles<=ser_wait_cycles+1; end if;
        end if;
    end process;
    assert link_poll_cycles<=134217728 report "Link polling interval exceeds counter width" severity failure;
    probe_errors<=error_record; probe_time<=time_record;
    probe_config<=config_record; probe_metadata<=config_meta;
    -- Read-clear counters must be published atomically with interval provenance.
    -- A failed/short read may have cleared the sink; the next interval is unknown.
    process(clk)
        variable status : std_logic_vector(7 downto 0);
        variable interval_ms : unsigned(31 downto 0);
    begin
        if rising_edge(clk) and enable_link_probe then
            if (not enable_ser and probe_ms_divider=99999) or
               (enable_ser and probe_ms_divider=ser_cycles_per_ms-1) then probe_ms_divider<=0; probe_ms<=probe_ms+1;
            else probe_ms_divider<=probe_ms_divider+1; end if;
            if next_state/=state and (state=read_link_config or state=read_symbol_errors)
               and hpd_present='1' and tx_ready='1' then
                status:=probe_reason & "1100";
                if probe_complete='1' then status:=x"0D"; end if;
                if state=read_link_config then
                    config_sequence<=config_sequence+1;
                    config_meta<=std_logic_vector(probe_ms) & status & std_logic_vector(config_sequence+1) & x"00";
                    config_record<=(others=>'0');
                    if probe_complete='1' then
                        config_meta(7 downto 0)<=payload_buffer(8);
                        for i in 0 to 7 loop config_record(i*8+7 downto i*8)<=payload_buffer(i); end loop;
                    end if;
                else
                    error_sequence<=error_sequence+1;
                    interval_ms:=(others=>'0');
                    if probe_complete='1' and previous_error_valid='1' then
                        status(1):='1'; interval_ms:=probe_ms-previous_error_ms;
                    end if;
                    error_record<=x"01" & status & std_logic_vector(error_sequence+1) & x"00000000";
                    time_record<=std_logic_vector(interval_ms) & std_logic_vector(probe_ms);
                    if probe_complete='1' then
                        for i in 0 to 3 loop error_record(i*8+7 downto i*8)<=payload_buffer(i); end loop;
                    end if;
                    previous_error_ms<=probe_ms; previous_error_valid<=probe_complete;
                end if;
            end if;
            if hpd_present='0' or tx_ready='0' or next_state=error or next_state=reset then
                error_record(50)<='0'; config_meta(26)<='0'; previous_error_valid<='0';
            end if;
        end if;
    end process;
    diagnostic_status <= last_reply & last_rx & std_logic_vector(failure_count) & last_failure &
                         std_logic_vector(rx_byte_count) & std_logic_vector(to_unsigned(t_state'pos(state),8));
    -- Passive observers only; never feed back into AUX control or timing.
    process(clk)
    begin
        if rising_edge(clk) then
            if next_state /= state and (next_state=error or next_state=edid_abort) then
                last_failure <= std_logic_vector(to_unsigned(t_state'pos(state),8));
                failure_count <= failure_count+1;
            end if;
            if channel_busy='0' and just_read_from_rx='1' then
                last_rx <= aux_rx_data;
                if rx_byte_count=0 then last_reply<=aux_rx_data; end if;
            end if;
        end if;
    end process;
    capabilities_reset <= '1' when state=reset or state=error or hpd_present='0' else '0';
    debug_pmod(0) <= debug_pmod_i(0);
    debug_pmod(1) <= status_de_active;
    debug_pmod(2) <= adjust_de_active;

i_aux_messages: dp_aux_messages port map (
           enhanced_framing => enhanced_framing,
           clk             => clk,
           -- Interface to send messages
           msg_de          => msg_de,
           msg             => msg,
           busy          => msg_busy,
           --- Interface to the AUX Channel
           aux_tx_wr_en => aux_tx_wr_en,
           aux_tx_data  => aux_tx_data
         );

i_channel: aux_interface port map (
           clk         => clk,
           debug_pmod  => debug_pmod_i,
           ------------------------------
           dp_tx_aux_p => dp_tx_aux_p,
           dp_tx_aux_n => dp_tx_aux_n,
           dp_rx_aux_p => dp_rx_aux_p,
           dp_rx_aux_n => dp_rx_aux_n,
           ------------------------------
           tx_wr_en    => aux_tx_wr_en,
           tx_data     => aux_tx_data,
           ------------------------------
           rx_rd_en    => aux_rx_rd_en,
           rx_data     => aux_rx_data,
           rx_empty    => aux_rx_empty,
           ------------------------------
           busy        => channel_busy,
           timeout     => channel_timeout
    );
    aux_rx_rd_en <= (not channel_busy) and (not aux_rx_empty)
                   when commit_remaining=0 else '0';

clk_proc: process(clK)
    begin
        if rising_edge(clk) then
            -------------------------------------------
            -- Are we going to change state this cycle?
            -------------------------------------------
            msg_de <= '0';
            retry_launch <= '0';
            if defer_pending = '1' then
                if defer_delay /= 0 then
                    defer_delay <= defer_delay - 1;
                elsif channel_busy = '0' and msg_busy = '0' then
                    retry_launch <= '1';
                    defer_pending <= '0';
                end if;
            end if;

            if next_state /= state or retry_launch = '1' then
                -------------------------------------------------------------
                -- Get ready to count how many reply bytes have been received
                -------------------------------------------------------------
                rx_byte_count <= (others => '0');
                reply_accepted <= '0';
                commit_remaining <= 0;
                commit_index <= 0;
                if retry_launch = '0' then
                    probe_complete<='0'; probe_reason<=x"5";
                    defer_retries <= 0;
                    i2c_retries <= 0;
                    defer_pending <= '0';
                    write_status_pending <= '0';
                end if;

                ---------------------------------------------------
                -- Controlling which FSM state to go to on success
                ---------------------------------------------------
                case next_state is
                    when reset              => state_on_success <= check_presence;
                    when check_presence     => state_on_success <= edid_block0;
                    when edid_block0        => state_on_success <= edid_block1;
                    when edid_block1        => state_on_success <= edid_block2;
                    when edid_block2        => state_on_success <= edid_block3;
                    when edid_block3        => state_on_success <= edid_block4;
                    when edid_block4        => state_on_success <= edid_block5;
                    when edid_block5        => state_on_success <= edid_block6;
                    when edid_block6        => state_on_success <= edid_block7;
                    when edid_block7        => state_on_success <= edid_stop;
                    when edid_stop          => state_on_success <= read_sink_count;
                    when edid_abort         => state_on_success <= error;
                    when read_sink_count    => state_on_success <= read_registers;
                    when read_registers     => state_on_success <= validate_capabilities;
                    when validate_capabilities => state_on_success <= error;
                    when read_sink_power    => state_on_success <= set_sink_d0;
                    when set_sink_d0        => state_on_success <= wait_sink_d0;
                    when wait_sink_d0       => state_on_success <= verify_sink_d0;
                    when verify_sink_d0     => state_on_success <= set_channel_coding;
                    when set_channel_coding => state_on_success <= set_speed_270;
                    when set_speed_270      => state_on_success <= set_downspread;
                    when set_downspread     => case link_count is
                                                   when "001"  => state_on_success <= set_link_count_1;
                                                   when "010"  => state_on_success <= set_link_count_2;
                                                   when "100"  => state_on_success <= set_link_count_4;
                                                   when others => state_on_success <= error;
                                               end case;
                    when set_link_count_1   => state_on_success <= wait_gtp_ready;
                    when set_link_count_2   => state_on_success <= wait_gtp_ready;
                    when set_link_count_4   => state_on_success <= wait_gtp_ready;
                    when wait_gtp_ready    => state_on_success <= error;
                    ------- Display Port clock training -------------------
                    when clock_training     => state_on_success <= clock_voltage_0p4;
                    when clock_voltage_0p4  => state_on_success <= clock_wait;
                    when clock_voltage_0p6  => state_on_success <= clock_wait;
                    when clock_voltage_0p8  => state_on_success <= clock_wait;
                    when clock_wait         => state_on_success <= clock_test;
                    when clock_test         => state_on_success <= clock_adjust;
                    when clock_adjust       => state_on_success <= clock_wait_after;
                    when clock_wait_after => state_on_success<=error; -- decided after consumers settle
                    ------- Display Port Alignment traning ------------
                    when align_training =>
                        -- Preserve the physical level already requested in CR.
                        -- Sink lane registers and GTP controls must agree.
                        if swing_0p8='1' then
                            if preemp_3p5='1' or preemp_6p0='1' then state_on_success<=align_p1_V0p8;
                            else state_on_success<=align_p0_V0p8; end if;
                        elsif swing_0p6='1' then
                            if preemp_6p0='1' then state_on_success<=align_p2_V0p6;
                            elsif preemp_3p5='1' then state_on_success<=align_p1_V0p6;
                            else state_on_success<=align_p0_V0p6; end if;
                        else
                            if preemp_6p0='1' then state_on_success<=align_p2_V0p4;
                            elsif preemp_3p5='1' then state_on_success<=align_p1_V0p4;
                            else state_on_success<=align_p0_V0p4; end if;
                        end if;
                    when align_p0_V0p4      => state_on_success <= align_wait0;
                    when align_p0_V0p6      => state_on_success <= align_wait0;
                    when align_p0_V0p8      => state_on_success <= align_wait0;
                    when align_p1_V0p4      => state_on_success <= align_wait0;
                    when align_p1_V0p6      => state_on_success <= align_wait0;
                    when align_p1_V0p8      => state_on_success <= align_wait0;
                    when align_p2_V0p4      => state_on_success <= align_wait0;
                    when align_p2_V0p6      => state_on_success <= align_wait0;
                    when align_p2_V0p8      => state_on_success <= align_wait0;
                    when align_wait0        => state_on_success <= align_test;
                    when align_test         => state_on_success <= align_adjust;
                    when align_adjust       => state_on_success <= align_wait_after;
                    when align_wait_after => state_on_success<=error; -- decided after consumers settle
                    when switch_to_normal   => state_on_success <= normal_lane_settings;
                    when normal_lane_settings => if enable_ser or disable_scrambling then state_on_success<=read_link_config;
                                                 else state_on_success <= link_established; end if;
                    when link_established   => state_on_success <= link_established;
                    when check_link         => state_on_success <= check_wait;
                    when check_wait         => if clock_locked  = '1' and equ_locked = '1' and symbol_locked = '1' and align_locked = '1' then
                                                  if enable_link_probe then state_on_success<=read_link_config;
                                                  else state_on_success <= link_established; end if;
                                               else
                                                  state_on_success <= error;
                                               end if;
                    when error              => state_on_success <= error;
                    when read_link_config   => state_on_success <= probe_config_done;
                    when read_symbol_errors => state_on_success <= probe_errors_done;
                    when probe_config_done  => if enable_ser then state_on_success<=link_established;
                                               else state_on_success <= read_symbol_errors; end if;
                    when probe_errors_done  => state_on_success <= link_established;

                    when others =>
                end case;

                ------------------------------------------------------------
                -- Controlling what message will be sent, how many words are
                -- expected back, and where it will be routed
                --
                -- NOTE: If you set 'expected' incorrectly then bytes will
                --       get left in the RX FIFO, potentially corrupting things
                ------------------------------------------------------------
                msg_de           <= '1';
                status_de_active <= '0';
                adjust_de_active <= '0';
                dp_reg_de_active <= '0';
                edid_de_active   <= '0';
                reset_addr_on_change <= '0';
                case next_state is
                    when reset                 => msg <= x"00"; expected <= x"00";
                    when check_presence        =>
                        if retry_launch='1' and write_status_pending='1' then msg<=x"12";
                        else msg<=x"01"; end if;
                        expected <= x"01"; reset_addr_on_change <= '1';

                    when edid_block0           => msg <= x"02"; expected <= x"11"; edid_de_active <= '1';
                    when edid_block1           => msg <= x"02"; expected <= x"11"; edid_de_active <= '1';
                    when edid_block2           => msg <= x"02"; expected <= x"11"; edid_de_active <= '1';
                    when edid_block3           => msg <= x"02"; expected <= x"11"; edid_de_active <= '1';
                    when edid_block4           => msg <= x"02"; expected <= x"11"; edid_de_active <= '1';
                    when edid_block5           => msg <= x"02"; expected <= x"11"; edid_de_active <= '1';
                    when edid_block6           => msg <= x"02"; expected <= x"11"; edid_de_active <= '1';
                    when edid_block7           => msg <= x"02"; expected <= x"11"; edid_de_active <= '1';
                    when edid_stop | edid_abort => msg <= x"13"; expected <= x"01";

                    when read_sink_count       => msg <= x"03"; expected <= x"02"; reset_addr_on_change <= '1';
                    when read_registers        => msg <= x"04"; expected <= x"11"; dp_reg_de_active <= '1';
                    when validate_capabilities => msg <= x"00"; expected <= x"00";
                    when set_channel_coding    => msg <= x"06"; expected <= x"01";
                    when read_sink_power       => msg <= x"1B"; expected <= x"02";
                        power_sequence<=power_sequence+1; power_flags<=x"00";
                        power_before<=x"00";power_after<=x"00";
                    when set_sink_d0           => msg <= x"1C"; expected <= x"01";
                    when wait_sink_d0          => msg <= x"00"; expected <= x"00";
                    when verify_sink_d0        => msg <= x"1D"; expected <= x"02";
                    when set_speed_270         => msg <= x"07"; expected <= x"01";
                    when set_downspread        => msg <= x"08"; expected <= x"01";
                    when set_link_count_1      => msg <= x"09"; expected <= x"01";
                    when set_link_count_2      => msg <= x"0A"; expected <= x"01";
                    when set_link_count_4      => msg <= x"0B"; expected <= x"01";
                    when wait_gtp_ready        => msg <= x"00"; expected <= x"00";

                    when clock_training        => msg <= x"0C"; expected <= x"01";
                    when clock_voltage_0p4     => msg <= lane_message(0,preemp_3p5,preemp_6p0); expected <= x"01";
                    when clock_voltage_0p6     => msg <= lane_message(1,preemp_3p5,preemp_6p0); expected <= x"01";
                    when clock_voltage_0p8     => msg <= lane_message(2,preemp_3p5,preemp_6p0); expected <= x"01";
                    when clock_wait            => msg <= x"00"; expected <= x"00";  reset_addr_on_change <= '1';
                    when clock_test            => msg <= x"0D"; expected <= x"09";  status_de_active <= '1'; reset_addr_on_change <= '1';
                    when clock_adjust          => msg <= x"0E"; expected <= x"03";  adjust_de_active <= '1';
                    when clock_wait_after      => msg <= x"00"; expected <= x"00";

                    when align_training        => msg <= x"0F"; expected <= x"01";
                    when align_p0_V0p4         => msg <= x"14"; expected <= x"01";
                    when align_p0_V0p6         => msg <= x"16"; expected <= x"01";
                    when align_p0_V0p8         => msg <= x"18"; expected <= x"01";
                    when align_p1_V0p4         => msg <= x"24"; expected <= x"01";
                    when align_p1_V0p6         => msg <= x"26"; expected <= x"01";
                    when align_p1_V0p8         => msg <= x"28"; expected <= x"01";
                    when align_p2_V0p4         => msg <= x"34"; expected <= x"01";
                    when align_p2_V0p6         => msg <= x"36"; expected <= x"01";
                    when align_p2_V0p8         => msg <= x"38"; expected <= x"01";
                    when align_wait0           => msg <= x"00"; expected <= x"00"; reset_addr_on_change <= '1';
                    when align_test            => msg <= x"0D"; expected <= x"09";  status_de_active <= '1'; reset_addr_on_change <= '1';
                    when align_adjust          => msg <= x"0E"; expected <= x"03";  adjust_de_active <= '1';
                    when align_wait_after      => msg <= x"00"; expected <= x"00";
                    when switch_to_normal      => if enable_ser then msg<=x"19";
                                                  elsif disable_scrambling then msg<=x"1A";
                                                  else msg <= x"11"; end if; expected <= x"01";
                    when ser_prepare           => msg<=x"00"; expected<=x"00";
                    -- DP 1.1a 3.5.1.3: every TRAINING_PATTERN_SET write is
                    -- followed by an enabled-lane settings burst. Replay the
                    -- trained setting; announce the link only after its ACK.
                    when normal_lane_settings  => msg <= last_lane_message; expected <= x"01";
                    when link_established      => msg <= x"00"; expected <= x"00"; reset_addr_on_change <= '1';
                    when check_link            => msg <= x"0D"; expected <= x"09"; status_de_active <= '1';
                    when check_wait            => msg <= x"00"; expected <= x"00";
                    when read_link_config      => msg <= x"15"; expected <= x"0A";
                    when read_symbol_errors    => msg <= x"17"; expected <= x"05";
                    when probe_config_done | probe_errors_done => msg<=x"00"; expected<=x"00";
                    when error                 => msg <= x"00";
                    when others                => msg <= x"00";
                end case;

                --------------------------------------------------------
                -- Set the control signals the state for the link state,
                -- transceivers andmain channel pipeline
                --------------------------------------------------------
                tx_powerup   <= '0';
                tx_clock_train <= '0';
                tx_align_train <= '0';
                tx_link_established <= '0';
                ser_arm<='0';
                case next_state is
                    when wait_gtp_ready        => tx_powerup <= '1';
                    when clock_training        => tx_powerup <= '1'; tx_clock_train <= '1';
                    when clock_voltage_0p4     => tx_powerup <= '1'; tx_clock_train <= '1';
                    when clock_voltage_0p6     => tx_powerup <= '1'; tx_clock_train <= '1';
                    when clock_voltage_0p8     => tx_powerup <= '1'; tx_clock_train <= '1';
                    when clock_wait            => tx_powerup <= '1'; tx_clock_train <= '1';
                    when clock_test            => tx_powerup <= '1'; tx_clock_train <= '1';
                    when clock_adjust          => tx_powerup <= '1'; tx_clock_train <= '1';
                    when clock_wait_after      => tx_powerup <= '1'; tx_clock_train <= '1';

                    when align_training        => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p0_V0p4         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p0_V0p6         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p0_V0p8         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p1_V0p4         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p1_V0p6         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p1_V0p8         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p2_V0p4         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p2_V0p6         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_p2_V0p8         => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_wait0           => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_test            => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_adjust          => tx_powerup <= '1'; tx_align_train <= '1';
                    when align_wait_after      => tx_powerup <= '1'; tx_align_train <= '1';
                    when switch_to_normal | normal_lane_settings | ser_prepare => tx_powerup <= '1';
                    when link_established      => tx_powerup <= '1'; tx_link_established <= '1';
                    when check_link            => tx_powerup <= '1'; tx_link_established <= '1';
                    when check_wait            => tx_powerup <= '1'; tx_link_established <= '1';
                    when read_link_config | read_symbol_errors | probe_config_done | probe_errors_done =>
                        tx_powerup <= '1';
                        if disable_scrambling then tx_link_established<=bypass_config_ok;
                        else tx_link_established<='1'; end if;
                    when others                => NULL;
                end case;
                if enable_ser and (next_state=ser_prepare or next_state=switch_to_normal or next_state=normal_lane_settings or
                    next_state=link_established or next_state=check_link or next_state=check_wait or next_state=read_link_config or
                    next_state=read_symbol_errors or next_state=probe_config_done or next_state=probe_errors_done) then ser_arm<='1'; end if;
            end if;

            --------------------------------------------------------
            -- Manage the small timer that counts how long we have
            -- been in the current state (used for implementing
            -- short waits for some FSM states)
            --------------------------------------------------------
            if state = next_state then
                count_100us <= count_100us - 1;
            else
                count_100us <= to_unsigned(9999,15);
                if reset_addr_on_change = '1' then
                    aux_addr_i <= (others => '0');
                end if;
            end if;
            state <= next_state;
            if msg_de='1' and (msg=x"14" or msg=x"16" or msg=x"18" or
                              msg=x"24" or msg=x"26" or msg=x"28" or
                              msg=x"34" or msg=x"36" or msg=x"38") then
                last_lane_message <= msg;
            end if;

            -------------------------------------------------------------
            -- How a short wait is implemented...
            --
            -- Has the 100us pause expired, when no data was expected?
            -- If so, move to the next test.
            -------------------------------------------------------------
            if next_state = state and state /= error and state /= wait_gtp_ready
               and state /= clock_wait and state /= align_wait0 and expected = x"00"
               and count_100us(count_100us'high) = '1' then
                if state=probe_config_done or state=probe_errors_done then
                    -- A malformed reply may leave a suffix in the FIFO. Drain it
                    -- without interpretation before starting the next request.
                    if channel_busy='0' and msg_busy='0' and aux_rx_empty='1' and just_read_from_rx='0' then
                        if enable_ser and state=probe_config_done and ser_config_ok='0' then next_state<=error;
                        elsif disable_scrambling and state=probe_config_done and bypass_config_ok='0' then next_state<=error;
                        else next_state<=state_on_success; end if;
                    end if;
                elsif state=ser_prepare then
                    if ser_ready_sync='1' then next_state<=switch_to_normal;
                    elsif ser_wait_cycles=9999999 then next_state<=error; end if;
                elsif state=validate_capabilities then
                    -- Consumers need a clock to latch the final committed byte.
                    -- Evaluate after the settling interval, not on state entry.
                    if sink_capable='1' and unsigned(training_interval)<=4 and (not enable_ser or dpcd_revision=x"11") then
                        if not initialize_sink_power or dpcd_revision=x"10" then next_state<=set_channel_coding;
                        elsif dpcd_revision=x"11" then next_state<=read_sink_power;
                        else next_state<=error;end if;
                    else next_state <= error;
                    end if;
                elsif state=wait_sink_d0 then
                    -- Conservative 1ms settling window at 100MHz; bounded and
                    -- interruptible by HPD/reset. DPCD 1.0 never accesses 600h.
                    if power_wait_cycles=99999 then next_state<=verify_sink_d0; end if;
                elsif state=clock_wait_after then
                    -- The final payload commit and level decoder take several
                    -- clocks. Choose the next setting here, after settling,
                    -- rather than latching stale flags on entry to the wait.
                    if clock_locked='1' then next_state<=align_training;
                    elsif cr_attempts=5 then next_state<=error;
                    elsif swing_0p8='1' then next_state<=clock_voltage_0p8;
                    elsif swing_0p6='1' then next_state<=clock_voltage_0p6;
                    else next_state<=clock_voltage_0p4; end if;
                elsif state=align_wait_after then
                    if clock_locked='0' then next_state<=error;
                    elsif equ_locked='1' and symbol_locked='1' and align_locked='1' then
                        if enable_ser then next_state<=ser_prepare; else next_state<=switch_to_normal; end if;
                    elsif eq_attempts=5 then next_state<=error;
                    elsif swing_0p8='1' then
                        if preemp_3p5='1' or preemp_6p0='1' then next_state<=align_p1_V0p8;
                        else next_state<=align_p0_V0p8; end if;
                    elsif swing_0p6='1' then
                        if preemp_6p0='1' then next_state<=align_p2_V0p6;
                        elsif preemp_3p5='1' then next_state<=align_p1_V0p6;
                        else next_state<=align_p0_V0p6; end if;
                    else
                        if preemp_6p0='1' then next_state<=align_p2_V0p4;
                        elsif preemp_3p5='1' then next_state<=align_p1_V0p4;
                        else next_state<=align_p0_V0p4; end if;
                    end if;
                else
                    next_state <= state_on_success;
                end if;
            end if;

            --------------------------------------------------------------
            -- Processing the data that has been received from the sink
            -- over the AUX channel. Only a complete, validated reply may
            -- update downstream capabilities or link-training state.
            --------------------------------------------------------------
            edid_de    <= '0';
            adjust_de  <= '0';
            dp_reg_de  <= '0';
            status_de  <= '0';
            if commit_remaining > 0 and next_state=state then
                edid_de <= edid_de_active;
                adjust_de <= adjust_de_active;
                dp_reg_de <= dp_reg_de_active;
                status_de <= status_de_active;
                aux_data <= payload_buffer(commit_index);
                aux_addr <= std_logic_vector(aux_addr_i);
                aux_addr_i <= aux_addr_i+1;
                commit_remaining <= commit_remaining-1;
                if commit_remaining=1 then
                    if state=read_link_config or state=read_symbol_errors then probe_complete<='1'; end if;
                    next_state <= state_on_success;
                    if state=read_sink_power then
                        power_before<=payload_buffer(0); power_flags(0)<='1';
                        -- VGA candidate only: a successful 0x00 pre-read must not
                        -- prevent an ACK-checked D0 write and settling delay.
                        if payload_buffer(0)/=x"00" and payload_buffer(0)/=x"01"
                           and payload_buffer(0)/=x"02" then next_state<=error; end if;
                    elsif state=verify_sink_d0 then
                        power_after<=payload_buffer(0);power_flags(2)<='1';
                        if payload_buffer(0)=x"01" then power_flags(3)<='1';
                        elsif payload_buffer(0)=x"00" and power_before=x"00"
                              and power_flags(1)='1' then
                            -- Candidate quirk: an adapter may ACK D0 while its
                            -- power register remains zero. Do not claim D0
                            -- readback; expose bit4 and require actual CR/EQ,
                            -- symbol/alignment lock and normal link monitoring.
                            power_flags(4)<='1';
                        else next_state<=error;end if;
                    end if;
                    if reset_addr_on_change='1' then
                        aux_addr_i <= (others => '0');
                    end if;
                else
                    commit_index <= commit_index+1;
                end if;
            end if;
            if channel_busy = '0' and state/=probe_config_done and state/=probe_errors_done then
                if just_read_from_rx = '1' then
                    if rx_byte_count = x"00" then
                        -- Validate both AUX and I2C reply bits, not ACK alone.
                        reply_accepted <= '0';
                        if aux_rx_data = x"00" then
                            reply_accepted <= '1';
                            if expected = x"01" and aux_rx_empty = '1' then
                                next_state <= state_on_success;
                                if state=set_sink_d0 then power_flags(1)<='1'; end if;
                            elsif state=check_presence and aux_rx_empty='0' then
                                null; -- Optional M byte: count of the one written byte.
                            elsif expected <= x"01" or expected > x"11"
                                  or aux_rx_empty = '1' then
                                reply_accepted <= '0';
                                next_state <= failure_state(state);
                            end if;
                        elsif ((aux_rx_data=x"20" and defer_retries<7) or
                               (aux_rx_data=x"80" and i2c_retries<7 and
                               state>=check_presence and state<=edid_abort)) and aux_rx_empty='1' then
                            if aux_rx_data=x"20" then defer_retries<=defer_retries+1;
                            else i2c_retries<=i2c_retries+1; end if;
                            defer_pending <= '1';
                            defer_delay <= 39999;
                            if aux_rx_data=x"80" and state=check_presence then
                                write_status_pending<='1';
                            end if;
                        else
                            if aux_rx_data=x"20" then probe_reason<=x"3"; else probe_reason<=x"2"; end if;
                            next_state <= failure_state(state);
                        end if;
                    elsif reply_accepted = '1' and next_state = state then
                        if state=check_presence then
                            reply_accepted<='0';
                            if rx_byte_count=1 and aux_rx_empty='1' and aux_rx_data=x"01" then
                                next_state<=state_on_success;
                            elsif rx_byte_count=1 and aux_rx_empty='1' and aux_rx_data=x"00"
                                  and i2c_retries<7 then
                                write_status_pending<='1';
                                i2c_retries<=i2c_retries+1;
                                defer_pending<='1'; defer_delay<=39999;
                            else next_state<=failure_state(state); end if;
                        else
                        if rx_byte_count < expected and rx_byte_count <= 16 then
                            payload_buffer(to_integer(rx_byte_count)-1) <= aux_rx_data;
                        end if;
                        if rx_byte_count = expected-1 and aux_rx_empty = '1'
                           and rx_byte_count <= 16 then
                            commit_index <= 0;
                            commit_remaining <= to_integer(rx_byte_count);
                            reply_accepted <= '0';
                        elsif rx_byte_count >= expected-1 or aux_rx_empty = '1' then
                            next_state <= failure_state(state);
                            reply_accepted <= '0';
                            commit_remaining <= 0;
                        end if;
                        end if;
                    end if;
                end if;
            end if;

            -----------------------------------------------------
            -- Manage the AUX channel timeout and the retry to
            -- establish a link.
            -------------------------------------------------------------
--            if channel_timeout = '1' or (state /= reset and state /= link_established and retry_now = '1') then
            if channel_timeout = '1' or (state /= reset      and state /= link_established and
                                             state /= check_link and state /= check_wait and
                                             state /= read_link_config and state /= read_symbol_errors and
                                             state /= probe_config_done and state /= probe_errors_done and retry_now = '1') then
                if state=read_link_config or state=read_symbol_errors then
                    -- Optional native reads must not reset an established link.
                    next_state<=failure_state(state); probe_reason<=x"4";
                elsif state=read_sink_power or state=set_sink_d0 or state=wait_sink_d0 or state=verify_sink_d0 then
                    -- A failed optional initialization attempt waits for the
                    -- normal bounded retry epoch; do not spin through reset.
                    next_state<=error;
                elsif state=probe_config_done or state=probe_errors_done then
                    null; -- Existing transport timeout can finish draining here.
                elsif channel_timeout='1' and state>=check_presence and state<=edid_abort then
                    next_state <= failure_state(state);
                    state<=error;
                else next_state <= reset; state<=error; end if;
                defer_pending <= '0';
                retry_launch <= '0';
                reply_accepted <= '0';
                commit_remaining <= 0;
                edid_de <= '0';
                dp_reg_de <= '0';
                adjust_de <= '0';
                status_de <= '0';
            end if;

            -------------------------------------------------
            -- If the link was established, then every
            -- now and then check the state of the link
            -------------------------------------------------
            if enable_ser and ser_request='1' and ser_accept='1' then next_state<=read_symbol_errors; end if;
            if state = link_established and (link_check_now = '1' or irq_pending = '1')
               and channel_busy = '0' and msg_busy = '0' then
                next_state <= check_link;
                irq_pending <= '0';
            end if;

            -------------------------------------------------
            -- If the full message has been received, then
            -- read any waiting data out of the FIFO.
            -- Also update the count of bytes read.
            -------------------------------------------------
            just_read_from_rx <= aux_rx_rd_en;
            if just_read_from_rx = '1' then
                rx_byte_count <= rx_byte_count+1;
            end if;

            -----------------------------------------
            -- Manage the reset timer
            -----------------------------------------
            if retry_count = 0 then
              retry_now   <= '1';
              retry_count <= to_unsigned(49999999,27);
            else
              retry_now   <= '0';
              retry_count <= retry_count - 1;
            end if;
            if link_check_count = 0 then
              link_check_now   <= '1';
              -- PPS actually became a 2Hz pulse....
              link_check_count <= to_unsigned(link_poll_cycles-1,27);
            else
              link_check_now   <= '0';
              link_check_count <= link_check_count - 1;
            end if;
            if hpd_irq = '1' then irq_pending <= '1'; end if;
            hpd_was_present <= hpd_present;
            -- State entry occurs after the sink ACKs its voltage/pattern write.
            -- Therefore the read interval never begins before programming ends.
            if next_state/=state then
                if next_state=clock_training then cr_attempts<=0; end if;
                if next_state=align_training then eq_attempts<=0; end if;
                if next_state=clock_test and cr_attempts<5 then cr_attempts<=cr_attempts+1; end if;
                if next_state=align_test and eq_attempts<5 then eq_attempts<=eq_attempts+1; end if;
                if next_state=clock_wait then
                    training_wait_cycles<=training_delay(training_interval,dpcd_revision,true);
                elsif next_state=align_wait0 then
                    training_wait_cycles<=training_delay(training_interval,dpcd_revision,false);
                end if;
            elsif state=clock_wait or state=align_wait0 then
                if training_wait_cycles=0 then next_state<=state_on_success;
                else training_wait_cycles<=training_wait_cycles-1;
                end if;
            end if;

            if state /= wait_gtp_ready then
                ready_wait_cycles <= 0;
            elsif next_state = state then
                if tx_ready = '1' then
                    next_state <= clock_training;
                elsif ready_wait_cycles = 999999 then
                    next_state <= error;
                else
                    ready_wait_cycles <= ready_wait_cycles + 1;
                end if;
            end if;

            -- tx_ready is synchronous to clk in this design (reset controller
            -- uses the same management clock). Never retain an established
            -- link or keep training after any active transmitter loses ready.
            -- The appended power states precede TX power-up operationally;
            -- keep them outside the legacy active-link ready-loss guard.
            if state >= clock_training and state <= ser_prepare and tx_ready = '0' then
                state <= error;
                next_state <= error;
                state_on_success <= error;
                msg_de <= '0';
                tx_powerup <= '0';
                tx_clock_train <= '0';
                tx_align_train <= '0';
                tx_link_established <= '0';
                ser_arm<='0';
                commit_remaining <= 0;
                edid_de <= '0'; dp_reg_de <= '0';
                adjust_de <= '0'; status_de <= '0';
            end if;

            if hpd_present = '0' then
                -- Drain/timeout any in-flight AUX transaction but launch no new one.
                state <= error;
                next_state <= error;
                state_on_success <= error;
                msg_de <= '0';
                tx_powerup <= '0';
                tx_clock_train <= '0';
                tx_align_train <= '0';
                tx_link_established <= '0';
                ser_arm<='0';
                irq_pending <= '0';
                edid_de <= '0';
                dp_reg_de <= '0';
                adjust_de <= '0';
                status_de <= '0';
                defer_pending <= '0';
                retry_launch <= '0';
                defer_retries <= 0;
                i2c_retries <= 0;
                reply_accepted <= '0';
                commit_remaining <= 0;
                commit_index <= 0;
                write_status_pending <= '0';
            elsif hpd_was_present = '0' then
                state <= error;
                next_state <= reset;
                irq_pending <= '0';
            end if;
            if enable_ser and ser_cancel='1' and state=read_symbol_errors then
                next_state<=error;
            end if;
        end if;
    end process;
end architecture;
