----------------------------------------------------------------------------------
-- Module Name: top_level - Behavioral
--
-- Description: Top level of my DisplayPort design.
--
----------------------------------------------------------------------------------
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
--  0.2 | 2015-09-29 | Updated for Opsis
------------------------------------------------------------------------------------

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity top_level is
    generic (stream_1080p60 : boolean := false;
             timestamp_scale : positive range 1 to 2048 := 1;
             link_probe_mode : boolean := false; ser_mode : boolean := false;
             disable_scrambling : boolean := false; gtp_health_mode : boolean := false;
             stream_capture_mode : boolean := false; initialize_sink_power : boolean := false;
             resolution_control_1280 : boolean := false;
             transfer_unit_size : positive range 32 to 64 := 40);
    port (
        clk100              : in    std_logic;
        diagnostic_tx       : out std_logic;
        debug               : out   std_logic_vector(7 downto 0) := (others => '0');
        ------------------------------
        gtptxp          : out   std_logic_vector(1 downto 0);
        gtptxn          : out   std_logic_vector(1 downto 0);

        refclk0_p       : in  STD_LOGIC;
        refclk0_n       : in  STD_LOGIC;

        ------------------------------
        dp_tx_hp_detect : in    std_logic;
        dp_tx_aux_p : out std_logic;
        dp_tx_aux_n : out std_logic;
        dp_rx_aux_p : in std_logic;
        dp_rx_aux_n : in std_logic
    );
end top_level;

architecture Behavioral of top_level is
   constant use_hw_8b10b_support : std_logic := '1'; -- Note HW 8b/10b not yet working for SPartan 6

	COMPONENT channel_management
    generic(enhanced_mode : boolean := false; enable_link_probe : boolean := false;
            link_poll_cycles : positive := 100000000;
            enable_ser : boolean := false; ser_cycles_per_ms : positive := 100000;
            disable_scrambling : boolean := false; initialize_sink_power : boolean := false);
	PORT(
		clk100               : IN std_logic;
      debug                : out std_logic_vector(7 downto 0);
      diagnostic_status    : out std_logic_vector(63 downto 0) := (others=>'0');
      sink_diagnostic : out std_logic_vector(63 downto 0) := (others=>'0');
      edid_block_diagnostic : out std_logic_vector(1023 downto 0) := (others=>'0');
      edid_metadata_diagnostic : out std_logic_vector(63 downto 0) := (others=>'0');
      probe_errors, probe_time, probe_config, probe_metadata : out std_logic_vector(63 downto 0) := (others=>'0');
      ser_arm : out std_logic := '0'; ser_stream_ready : in std_logic := '0';
      ser_diagnostic : out std_logic_vector(383 downto 0) := (others=>'0');
      sink_power_status : out std_logic_vector(63 downto 0) := (others=>'0');
		hpd                  : IN std_logic;
		stream_channel_count : IN std_logic_vector(2 downto 0);
		source_channel_count : IN std_logic_vector(2 downto 0);
		tx_clock_train       : out std_logic;
		tx_align_train       : out std_logic;
		tx_running           : IN std_logic_vector(3 downto 0);
		aux_tx_p             : OUT std_logic;
		aux_tx_n             : OUT std_logic;
		aux_rx_p             : IN std_logic;
		aux_rx_n             : IN std_logic;
		tx_powerup_channel   : OUT std_logic_vector(3 downto 0);
		tx_preemp_0p0        : OUT std_logic;
		tx_preemp_3p5        : OUT std_logic;
		tx_preemp_6p0        : OUT std_logic;
		tx_swing_0p4         : OUT std_logic;
		tx_swing_0p6         : OUT std_logic;
		tx_swing_0p8         : OUT std_logic;
      tx_link_established  : OUT std_logic
		);
	END COMPONENT;

    component test_source is
        port (
            clk          : in  std_logic;
            stream_channel_count : out std_logic_vector(2 downto 0);
            ready        : out std_logic;
            data         : out std_logic_vector(72 downto 0)
        );
    end component;

    -- Optional diagnostic source is compiled by its separate audit entry point.
    component dp_1280x1024_source is
        generic (initial_frame : natural range 0 to 65535 := 0);
        port (clk : in std_logic; reset : in std_logic := '0';
              stream_channel_count : out std_logic_vector(2 downto 0);
              ready : out std_logic; data : out std_logic_vector(72 downto 0));
    end component;

    COMPONENT main_stream_processing
        generic( use_hw_8b10b_support : std_logic; enhanced_mode : boolean := false;
                 two_lane_stream : boolean := false; ser_mode : boolean := false;
                 disable_scrambling : boolean := false);
        PORT(
           symbol_clk          : IN  std_logic;
           tx_link_established : IN  std_logic;
           source_ready        : IN  std_logic;
           tx_clock_train      : IN  std_logic;
           tx_align_train      : IN  std_logic;
           in_data             : IN  std_logic_vector(72 downto 0);
           tx_symbols          : OUT std_logic_vector(79 downto 0);
           ser_arm : in std_logic := '0'; ser_ready : out std_logic := '0'
        );
    END COMPONENT;

    component Transceiver is
    generic( use_hw_8b10b_support : std_logic);
    Port ( mgmt_clk        : in  STD_LOGIC;
           powerup_channel : in  STD_LOGIC_vector;
           debug           : out std_logic_vector(7 downto 0);

           preemp_0p0      : in  STD_LOGIC;
           preemp_3p5      : in  STD_LOGIC;
           preemp_6p0      : in  STD_LOGIC;

           swing_0p4       : in  STD_LOGIC;
           swing_0p6       : in  STD_LOGIC;
           swing_0p8       : in  STD_LOGIC;

           tx_running      : out STD_LOGIC_vector;

           symbolclk      : out STD_LOGIC;

           in_symbols     : in  std_logic_vector(79 downto 0);

           refclk0_p       : in  STD_LOGIC;
           refclk0_n       : in  STD_LOGIC;



           gtptxp         : out std_logic_vector(1 downto 0);
           gtptxn         : out std_logic_vector(1 downto 0);
           tx_buffer_status : out std_logic_vector(7 downto 0) := (others=>'0'));
    end component;

    component video_generator is
    Port (  clk              : in  STD_LOGIC;
            h_visible_len    : in  std_logic_vector(11 downto 0) := (others => '0');
            h_blank_len      : in  std_logic_vector(11 downto 0) := (others => '0');
            h_front_len      : in  std_logic_vector(11 downto 0) := (others => '0');
            h_sync_len       : in  std_logic_vector(11 downto 0) := (others => '0');

            v_visible_len    : in  std_logic_vector(11 downto 0) := (others => '0');
            v_blank_len      : in  std_logic_vector(11 downto 0) := (others => '0');
            v_front_len      : in  std_logic_vector(11 downto 0) := (others => '0');
            v_sync_len       : in  std_logic_vector(11 downto 0) := (others => '0');

            vid_blank        : out STD_LOGIC;
            vid_hsync        : out STD_LOGIC;
            vid_vsync        : out STD_LOGIC);
    end component;
    --------------------------------------------------------------------------
    signal tx_powerup       : std_logic := '0';
    signal tx_clock_train   : std_logic := '0';
    signal tx_align_train   : std_logic := '0';

    ---------------------------------------------
    -- Transceiver signals
    ---------------------------------------------
    signal txresetdone      : std_logic := '0';
    signal txoutclk         : std_logic := '0';
    signal symbolclk        : std_logic := '0';

    signal tx_running       : std_logic_vector(3 downto 0) := (others => '0');
    signal tx_powerup_channel  : std_logic_vector(3 downto 0);

    signal tx_preemp_0p0    : std_logic := '1';
    signal tx_preemp_3p5    : STD_LOGIC := '0';
    signal tx_preemp_6p0    : STD_LOGIC := '0';

    signal tx_swing_0p4     : STD_LOGIC := '1';
    signal tx_swing_0p6     : STD_LOGIC := '0';
    signal tx_swing_0p8     : STD_LOGIC := '0';

    ------------------------------------------------
    signal tx_link_established : std_logic := '0';
    ------------------------------------------------
    signal tx_debug        : std_logic_vector(7 downto 0);
    signal mgmt_debug:std_logic_vector(7 downto 0);
    signal diagnostic_status : std_logic_vector(63 downto 0);
    signal sink_power_status : std_logic_vector(63 downto 0);
    signal sink_diagnostic : std_logic_vector(63 downto 0);
    signal diagnostic_payload : std_logic_vector(63 downto 0);
    signal probe_errors, probe_time, probe_config, probe_metadata : std_logic_vector(63 downto 0);
    signal ser_arm,ser_ready : std_logic;
    signal ser_diagnostic : std_logic_vector(383 downto 0);
    signal diagnostic_kind : std_logic_vector(7 downto 0);
    signal edid_block_diagnostic : std_logic_vector(1023 downto 0);
    signal edid_metadata_diagnostic, counter_status : std_logic_vector(63 downto 0);
    signal stream_counters : std_logic_vector(159 downto 0);
    signal source_ready_sync : std_logic;
    signal tx_buffer_status : std_logic_vector(7 downto 0);
    signal gtp_health : std_logic_vector(63 downto 0);
    signal capture_data:std_logic_vector(39 downto 0);
    signal capture_address:unsigned(11 downto 0);
    signal capture_bank:unsigned(1 downto 0);
    signal capture_complete:std_logic_vector(2 downto 0);
    signal capture_payload:std_logic_vector(63 downto 0);
    signal capture_kind:std_logic_vector(7 downto 0);

    constant source_channel_count : std_logic_vector(2 downto 0) := "010";
    signal stream_channel_count : std_logic_vector(2 downto 0) := "000";
    signal test_signal_ready      : std_logic;

    signal msa_merged_data     : std_logic_vector(72 downto 0) := (others => '0');  -- With switching point
    signal tx_symbols          : std_logic_vector(79 downto 0) := (others => '0');

    constant BE     : std_logic_vector(8 downto 0) := "111111011";   -- K27.7
    constant BS     : std_logic_vector(8 downto 0) := "110111100";   -- K28.5
    constant SR     : std_logic_vector(8 downto 0) := "100011100";   -- K28.0

begin
    -- Separate diagnostic profile. The legacy stream_1080p60 flag continues
    -- to select the existing two-lane enhanced control/telemetry path.
    assert not resolution_control_1280 or
        (stream_1080p60 and link_probe_mode and gtp_health_mode and
         not ser_mode and not disable_scrambling and timestamp_scale=1 and transfer_unit_size=64)
        report "1280 control requires unchanged two-lane HBR TU64 diagnostic path" severity failure;
    assert not ser_mode or (stream_1080p60 and link_probe_mode) report "SER requires fixed two-lane enhanced diagnostic configuration" severity failure;
    assert not disable_scrambling or (stream_1080p60 and link_probe_mode and not ser_mode)
        report "Bypass requires an independent two-lane video diagnostic" severity failure;
    assert not stream_capture_mode or gtp_health_mode report "Capture requires normal video and GTP health" severity failure;
    g_uart_normal:if not stream_capture_mode generate
        i_diagnostic_uart: entity work.diagnostic_uart port map (
            clk=>clk100, status=>diagnostic_payload, record_kind=>diagnostic_kind, tx=>diagnostic_tx);
    end generate;
    g_capture:if stream_capture_mode generate
        memory:entity work.stream_window_capture port map(symbol_clk=>symbolclk,mgmt_clk=>clk100,
            symbols=>tx_symbols(39 downto 0),read_address=>capture_address,read_bank=>capture_bank,
            read_data=>capture_data,complete=>capture_complete);
        mux:entity work.stream_capture_mux port map(clk=>clk100,normal_kind=>diagnostic_kind,
            normal_payload=>diagnostic_payload,complete=>capture_complete,read_data=>capture_data,
            read_address=>capture_address,read_bank=>capture_bank,kind=>capture_kind,payload=>capture_payload);
        uart:entity work.diagnostic_uart generic map(interval_cycles=>200000) port map(
            clk=>clk100,status=>capture_payload,record_kind=>capture_kind,tx=>diagnostic_tx);
    end generate;
    i_stream_diagnostic: entity work.stream_diagnostic port map(
        symbol_clk=>symbolclk,mgmt_clk=>clk100,source_ready=>test_signal_ready,
        source_data=>msa_merged_data,tx_symbols=>tx_symbols,
        counters=>stream_counters,ready_sync=>source_ready_sync);
    counter_status <= x"000" & tx_running(1) & tx_align_train & tx_clock_train & tx_link_established &
        "000000000000000" & source_ready_sync & stream_counters(159 downto 128);
    assert not gtp_health_mode or (stream_1080p60 and link_probe_mode and not ser_mode and not disable_scrambling)
        report "GTP health requires normal scrambled two-lane video diagnostic" severity failure;
    g_health: if gtp_health_mode generate
        i_gtp_health: entity work.gtp_health_monitor port map(symbol_clk=>symbolclk,mgmt_clk=>clk100,
            buffer_status=>tx_buffer_status(3 downto 0),running=>tx_running(1 downto 0),
            link_ready=>tx_link_established,diagnostic=>gtp_health);
        mux: entity work.gtp_diagnostic_mux generic map(enable_link_probe=>true,enable_sink_power=>initialize_sink_power) port map(
            sink_power=>sink_power_status,
            probe_errors=>probe_errors,probe_time=>probe_time,probe_config=>probe_config,probe_metadata=>probe_metadata,
            clk=>clk100,aux_status=>diagnostic_status,counter0=>stream_counters(63 downto 0),
            counter1=>stream_counters(127 downto 64),counter2=>counter_status,health=>gtp_health,
            sink_status=>sink_diagnostic,edid_block=>edid_block_diagnostic,
            edid_metadata=>edid_metadata_diagnostic,kind=>diagnostic_kind,payload=>diagnostic_payload);
    end generate;
    g_normal_diagnostic: if not ser_mode and not gtp_health_mode generate
    i_diagnostic_mux: entity work.diagnostic_mux generic map(enable_link_probe=>link_probe_mode) port map(
        probe_errors=>probe_errors, probe_time=>probe_time, probe_config=>probe_config, probe_metadata=>probe_metadata,
        clk=>clk100, aux_status=>diagnostic_status, counter0=>stream_counters(63 downto 0),
        counter1=>stream_counters(127 downto 64), counter2=>counter_status,
        sink_status=>sink_diagnostic, edid_block=>edid_block_diagnostic,
        edid_metadata=>edid_metadata_diagnostic, kind=>diagnostic_kind, payload=>diagnostic_payload);
    end generate;
    g_ser_diagnostic: if ser_mode generate
        mux: entity work.ser_diagnostic_mux port map(clk=>clk100,aux_status=>diagnostic_status,
            sink_status=>sink_diagnostic,config=>probe_config,metadata=>probe_metadata,
            ser_sample=>ser_diagnostic,kind=>diagnostic_kind,payload=>diagnostic_payload);
    end generate;

i_channel_management: channel_management generic map(enhanced_mode=>stream_1080p60, enable_link_probe=>link_probe_mode,
    enable_ser=>ser_mode,disable_scrambling=>disable_scrambling,initialize_sink_power=>initialize_sink_power) PORT MAP(
        sink_power_status=>sink_power_status,
        ser_arm=>ser_arm,ser_stream_ready=>ser_ready,ser_diagnostic=>ser_diagnostic,
        probe_errors=>probe_errors, probe_time=>probe_time, probe_config=>probe_config, probe_metadata=>probe_metadata,
		clk100               => clk100,
        debug                => mgmt_debug,
        diagnostic_status    => diagnostic_status,
        sink_diagnostic      => sink_diagnostic,
        edid_block_diagnostic => edid_block_diagnostic,
        edid_metadata_diagnostic => edid_metadata_diagnostic,
		hpd                  => dp_tx_hp_detect,
		aux_tx_p             => dp_tx_aux_p,
		aux_tx_n             => dp_tx_aux_n,
		aux_rx_p             => dp_rx_aux_p,
		aux_rx_n             => dp_rx_aux_n,
		stream_channel_count => stream_channel_count,
		source_channel_count => source_channel_count,
		tx_clock_train       => tx_clock_train,
		tx_align_train       => tx_align_train,
		tx_powerup_channel   => tx_powerup_channel,
		tx_preemp_0p0        => tx_preemp_0p0,
		tx_preemp_3p5        => tx_preemp_3p5,
		tx_preemp_6p0        => tx_preemp_6p0,
		tx_swing_0p4         => tx_swing_0p4,
		tx_swing_0p6         => tx_swing_0p6,
		tx_swing_0p8         => tx_swing_0p8,
		tx_running           => tx_running,
      tx_link_established  => tx_link_established
	);

g_reference: if not stream_1080p60 generate
i_test_source: test_source port map (
            clk                  => symbolclk,
            stream_channel_count => stream_channel_count,
            ready                => test_signal_ready,
            data                 => msa_merged_data
        );
end generate;

g_1080p60: if stream_1080p60 and not resolution_control_1280 generate
    i_test_source: entity work.dp_1080p60_source generic map(timestamp_scale=>timestamp_scale,transfer_unit_size=>transfer_unit_size) port map (
        clk => symbolclk, reset => '0', stream_channel_count => stream_channel_count,
        ready => test_signal_ready, data => msa_merged_data);
end generate;

g_1280_control: if stream_1080p60 and resolution_control_1280 generate
    i_test_source: dp_1280x1024_source port map (
        clk => symbolclk, reset => '0', stream_channel_count => stream_channel_count,
        ready => test_signal_ready, data => msa_merged_data);
end generate;


----------------------------------------------------------------------
Inst_main_stream_processing: main_stream_processing generic map (
      enhanced_mode => stream_1080p60, ser_mode=>ser_mode,disable_scrambling=>disable_scrambling,
      two_lane_stream => stream_1080p60,
      use_hw_8b10b_support => use_hw_8b10b_support
   ) PORT MAP(
        ser_arm=>ser_arm,ser_ready=>ser_ready,
		symbol_clk          => symbolclk,
		tx_link_established => tx_link_established,
		source_ready        => test_signal_ready,
		tx_clock_train      => tx_clock_train,
		tx_align_train      => tx_align_train,
		in_data             => msa_merged_data,
		tx_symbols          => tx_symbols
	);

i_tx0: Transceiver generic map (
      use_hw_8b10b_support => use_hw_8b10b_support
   ) Port map (
       mgmt_clk        => clk100,
       powerup_channel => tx_powerup_channel,
       tx_running      => tx_running,
       debug           => tx_debug,

       preemp_0p0      => tx_preemp_0p0,
       preemp_3p5      => tx_preemp_3p5,
       preemp_6p0      => tx_preemp_6p0,

       swing_0p4       => tx_swing_0p4,
       swing_0p6       => tx_swing_0p6,
       swing_0p8       => tx_swing_0p8,

       in_symbols      => tx_symbols,
       tx_buffer_status => tx_buffer_status,

       gtptxp          => gtptxp,
       gtptxn          => gtptxn,
       symbolclk       => symbolclk,

       refclk0_p       => refclk0_p,
       refclk0_n       => refclk0_n);


--    debug(0) <= tx_link_established;
    debug(0) <= mgmt_debug(0);
--   debug(0) <= tx_clock_train;


--process(symbolclk)
--   begin
--
--      -- SHow the HBLANK as a debug of the video stream.
--      if rising_edge(symbolclk) then
--         -- Look for BS symbols
--         if sr_inserted_data(8 downto 0) = "110111100" or sr_inserted_data(17 downto 9) = "110111100" then
--            debug(0) <= toggle and tx_link_established; --'1';
--            toggle <= not toggle;
--         end if;
--         -- Look for BE symbols
--         if sr_inserted_data(8 downto 0) = "111111011" or sr_inserted_data(17 downto 9) = "111111011" then
--            debug(0) <= toggle and tx_link_established; --'0';
--            toggle <= not toggle;
--         end if;
--      end if;
--   end process;
--process(gclk)
--   begin
--      if rising_edge(gclk) then
--         count <= count + 1;
--         case count(10 downto 8) is
--            when "000"  => debug(0) <= not count(7);
--            when "001"  => debug(0) <= tx_debug(0);
--            when "010"  => debug(0) <= tx_debug(1);
--            when "011"  => debug(0) <= tx_debug(2);
--            when "100"  => debug(0) <= tx_debug(3);
--            when "101"  => debug(0) <= tx_debug(4);
--            when "110"  => debug(0) <= tx_debug(5);
--            when others => debug(0) <= tx_debug(6);
--         end case;
--      end if;
--   end process;
end Behavioral;
