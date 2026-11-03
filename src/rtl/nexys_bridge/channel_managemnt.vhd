----------------------------------------------------------------------------------
-- Module Name: channel_management - Behavioral
--
-- Description: Manages standing up the DisplayPort Channel
--
----------------------------------------------------------------------------------
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
--  0.1 | 2015-10-17 | Initial Version
------------------------------------------------------------------------------------

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity channel_management is
    generic(enhanced_mode : boolean := false; enable_link_probe : boolean := false;
            link_poll_cycles : positive := 100000000;
            enable_ser : boolean := false; ser_cycles_per_ms : positive := 100000;
            disable_scrambling : boolean := false; initialize_sink_power : boolean := false);
    Port ( clk100   : in  STD_LOGIC;
           debug    : out std_logic_vector(7 downto 0);
           diagnostic_status : out std_logic_vector(63 downto 0) := (others=>'0');
           sink_diagnostic : out std_logic_vector(63 downto 0) := (others=>'0');
           edid_block_diagnostic : out std_logic_vector(1023 downto 0) := (others=>'0');
           edid_metadata_diagnostic : out std_logic_vector(63 downto 0) := (others=>'0');
           probe_errors, probe_time, probe_config, probe_metadata : out std_logic_vector(63 downto 0) := (others=>'0');
           ser_arm : out std_logic := '0';
           ser_stream_ready : in std_logic := '0';
           ser_diagnostic : out std_logic_vector(383 downto 0) := (others=>'0');
           sink_power_status : out std_logic_vector(63 downto 0) := (others=>'0');

           hpd      : in    std_logic;
           aux_tx_p : out std_logic;
           aux_tx_n : out std_logic;
           aux_rx_p : in std_logic;
           aux_rx_n : in std_logic;

           -- Datapath requirements
           stream_channel_count : std_logic_vector(2 downto 0);
           source_channel_count : std_logic_vector(2 downto 0);

           -- Datapath control
           tx_clock_train        : out  std_logic;
           tx_align_train        : out  std_logic;

           -- Transceiver management
           tx_powerup_channel : out STD_LOGIC_VECTOR(3 downto 0) := (others =>'0');

           tx_preemp_0p0      : out STD_LOGIC := '0';
           tx_preemp_3p5      : out STD_LOGIC := '0';
           tx_preemp_6p0      : out STD_LOGIC := '0';

           tx_swing_0p4       : out STD_LOGIC := '0';
           tx_swing_0p6       : out STD_LOGIC := '0';
           tx_swing_0p8       : out STD_LOGIC := '0';

           tx_running      : in  std_logic_vector(3 downto 0);
           tx_link_established : OUT std_logic
           );
end channel_management;

architecture Behavioral of channel_management is

    component hotplug_decode is
        port (clk     : in  std_logic;
              hpd     : in  std_logic;
              irq     : out std_logic := '0';
              present : out std_logic := '0');
    end component;

    component test_source is
        port (
            clk          : in  std_logic;
            ready        : out std_logic;
            data         : out std_logic_vector(72 downto 0)
        );
    end component;

    component link_signal_mgmt is
        Port ( mgmt_clk    : in  STD_LOGIC;

               tx_powerup  : in  STD_LOGIC;  -- Used to requiest the powerup of all transceives

               status_de   : in  std_logic;
               adjust_de   : in  std_logic;
               addr        : in  std_logic_vector(7 downto 0);
                 data        : in  std_logic_vector(7 downto 0);

               sink_channel_count   : in  std_logic_vector(2 downto 0);
               source_channel_count : in  std_logic_vector(2 downto 0);
               stream_channel_count : in  std_logic_vector(2 downto 0);
               active_channel_count : out std_logic_vector(2 downto 0);

               powerup_channel : out std_logic_vector(3 downto 0); -- used to request powerup individuat channels

               clock_locked   : out STD_LOGIC;
               equ_locked     : out STD_LOGIC;
               symbol_locked  : out STD_LOGIC;
               align_locked   : out STD_LOGIC;

               preemp_0p0  : out STD_LOGIC;
               preemp_3p5  : out STD_LOGIC;
               preemp_6p0  : out STD_LOGIC;

               swing_0p4   : out STD_LOGIC;
               swing_0p6   : out STD_LOGIC;
               swing_0p8   : out STD_LOGIC);
    end component;

   component aux_channel is
        generic(enable_link_probe : boolean := false; link_poll_cycles : positive := 100000000;
                enable_ser : boolean := false; ser_cycles_per_ms : positive := 100000;
                disable_scrambling : boolean := false; initialize_sink_power : boolean := false);
      port (
         clk                 : in    std_logic;
         debug_pmod          : out   std_logic_vector(7 downto 0);
         diagnostic_status : out std_logic_vector(47 downto 0) := (others=>'0');
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
         -- Hot plug signals
         hpd_irq             : in std_logic;
         hpd_present         : in std_logic;
         sink_capable        : in std_logic := '1';
         tx_ready            : in std_logic := '1';
         training_interval   : in std_logic_vector(6 downto 0) := (others=>'0');
         dpcd_revision       : in std_logic_vector(7 downto 0) := x"12";
         capabilities_reset  : out std_logic := '1';

         ------------------------------
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
         tx_powerup          : out   std_logic := '0';
         tx_clock_train      : out   std_logic := '0';
         tx_align_train      : out   std_logic := '0';
         tx_link_established : out   std_logic := '0';
         ------------------------------
         dp_tx_hp_detect : in    std_logic;
         dp_tx_aux_p     : out std_logic;
         dp_tx_aux_n     : out std_logic;
         dp_rx_aux_p     : in std_logic;
         dp_rx_aux_n     : in std_logic
        );
    end component;

    component edid_decode is
       port ( clk              : in std_logic;

              edid_de          : in std_logic;
              edid_data        : in std_logic_vector(7 downto 0);
              edid_addr        : in std_logic_vector(7 downto 0);
              invalidate       : in std_logic;

              valid            : out std_logic := '0';

              support_RGB444   : out std_logic := '0';
              support_YCC444   : out std_logic := '0';
              support_YCC422   : out std_logic := '0';

              pixel_clock_x10k : out std_logic_vector(15 downto 0) := (others => '0');

              h_visible_len    : out std_logic_vector(11 downto 0) := (others => '0');
              h_blank_len      : out std_logic_vector(11 downto 0) := (others => '0');
              h_front_len      : out std_logic_vector(11 downto 0) := (others => '0');
              h_sync_len       : out std_logic_vector(11 downto 0) := (others => '0');

              v_visible_len    : out std_logic_vector(11 downto 0) := (others => '0');
              v_blank_len      : out std_logic_vector(11 downto 0) := (others => '0');
              v_front_len      : out std_logic_vector(11 downto 0) := (others => '0');
              v_sync_len       : out std_logic_vector(11 downto 0) := (others => '0');
              interlaced       : out std_logic := '0');
    end component;

    component dp_register_decode is
       port ( clk         : in std_logic;

              de          : in std_logic;
              data        : in std_logic_vector(7 downto 0);
              addr        : in std_logic_vector(7 downto 0);
              invalidate  : in std_logic;

              valid              : out std_logic := '0';

              revision           : out std_logic_vector(7 downto 0) := (others => '0');
              link_rate_2_70     : out std_logic := '0';
              link_rate_1_62     : out std_logic := '0';
              extended_framing   : out std_logic := '0';
              link_count         : out std_logic_vector(3 downto 0) := (others => '0');
              max_downspread     : out std_logic_vector(7 downto 0) := (others => '0');
              coding_supported   : out std_logic_vector(7 downto 0) := (others => '0');
              port0_capabilities : out std_logic_vector(15 downto 0) := (others => '0');
              port1_capabilities : out std_logic_vector(15 downto 0) := (others => '0');
              norp               : out std_logic_vector(7 downto 0) := (others => '0');
              training_interval  : out std_logic_vector(6 downto 0) := (others=>'0')
        );
    end component;

    component training_and_channel_delay is
    port (
        clk                : in  std_logic;
        channel_delay      : in  std_logic_vector(1 downto 0);
        clock_train        : in  std_logic;
        align_train        : in  std_logic;

        in_data            : in  std_logic_vector(17 downto 0);
        out_data           : out std_logic_vector(17 downto 0);
        out_data0forceneg  : out std_logic;
        out_data1forceneg  : out std_logic
    );
    end component;

    signal edid_de          : std_logic;
    signal dp_reg_de        : std_logic;
    signal adjust_de        : std_logic;
    signal status_de        : std_logic;
    signal aux_data         : std_logic_vector(7 downto 0);
    signal aux_addr         : std_logic_vector(7 downto 0);
    signal invalidate       : std_logic;
    signal tx_powerup       : std_logic;

    signal preemp_0p0_i     : STD_LOGIC := '0';
    signal preemp_3p5_i     : STD_LOGIC := '0';
    signal preemp_6p0_i     : STD_LOGIC := '0';

    signal swing_0p4_i      : STD_LOGIC := '0';
    signal swing_0p6_i      : STD_LOGIC := '0';
    signal swing_0p8_i      : STD_LOGIC := '0';

    signal support_RGB444   : std_logic := '0';
    signal support_YCC444   : std_logic := '0';
    signal support_YCC422   : std_logic := '0';

    --------------------------------------------
    -- EDID data
    ---------------------------------------------
    signal edid_valid       : std_logic := '0';
    signal pixel_clock_x10k : std_logic_vector(15 downto 0) := (others => '0');

    signal h_visible_len    : std_logic_vector(11 downto 0) := (others => '0');
    signal h_blank_len      : std_logic_vector(11 downto 0) := (others => '0');
    signal h_front_len      : std_logic_vector(11 downto 0) := (others => '0');
    signal h_sync_len       : std_logic_vector(11 downto 0) := (others => '0');

    signal v_visible_len    : std_logic_vector(11 downto 0) := (others => '0');
    signal v_blank_len      : std_logic_vector(11 downto 0) := (others => '0');
    signal v_front_len      : std_logic_vector(11 downto 0) := (others => '0');
    signal v_sync_len       : std_logic_vector(11 downto 0) := (others => '0');
    signal interlaced       : std_logic := '0';
    --------------------------------------------
    -- Display port data
    ---------------------------------------------
    signal dp_valid              : std_logic := '0';
    signal dp_revision           : std_logic_vector(7 downto 0) := (others => '0');
    signal dp_link_rate_2_70     : std_logic := '0';
    signal dp_link_rate_1_62     : std_logic := '0';
    signal dp_extended_framing   : std_logic := '0';
    signal dp_link_count         : std_logic_vector(3 downto 0) := (others => '0');
    signal dp_max_downspread     : std_logic_vector(7 downto 0) := (others => '0');
    signal dp_coding_supported   : std_logic_vector(7 downto 0) := (others => '0');
    signal dp_port0_capabilities : std_logic_vector(15 downto 0) := (others => '0');
    signal dp_port1_capabilities : std_logic_vector(15 downto 0) := (others => '0');
    signal dp_norp               : std_logic_vector(7 downto 0) := (others => '0');
    --------------------------------------------------------------------------

    ---------------------------------------------
    -- Transceiver signals
    ---------------------------------------------
    signal txresetdone      : std_logic := '0';
    signal txoutclk         : std_logic := '0';
    signal symbolclk        : std_logic := '0';

    signal clock_locked    : std_logic := '0';
    signal equ_locked      : std_logic := '0';
    signal symbol_locked   : std_logic := '0';
    signal align_locked    : std_logic := '0';
    ------------------------------------------------
    signal interface_debug : std_logic_vector(7 downto 0);
    signal mgmt_debug        : std_logic_vector(7 downto 0);

    signal sink_channel_count   : std_logic_vector(2 downto 0) := "000";
    signal active_channel_count : std_logic_vector(2 downto 0) := "000";

    signal hpd_irq     : std_logic;
    signal hpd_present : std_logic;
    signal capabilities_invalid : std_logic;
    signal aux_capabilities_reset : std_logic;
    signal sink_capable : std_logic;
    signal dp_training_interval : std_logic_vector(6 downto 0) := (others=>'0');
    signal active_tx_ready : std_logic;
    signal framing_enabled : std_logic;
    signal sink_status_bytes : std_logic_vector(63 downto 0) := (others=>'0');
    signal sink_status_valid : std_logic := '0';
    signal sink_status_seq : unsigned(15 downto 0) := (others=>'0');
    signal sink_status_next : natural range 0 to 7 := 0;
    signal sink_status_receiving : std_logic := '0';
    signal sink_status_pending : std_logic_vector(63 downto 0) := (others=>'0');
    signal aux_diagnostic : std_logic_vector(47 downto 0);

begin
    framing_enabled <= '1' when enhanced_mode else '0';
    sink_diagnostic <= std_logic_vector(sink_status_seq) & sink_status_bytes(7 downto 0) &
        sink_status_bytes(39 downto 32) & sink_status_bytes(23 downto 16) & sink_status_bytes(47 downto 40) &
        dp_link_count & sink_status_valid & dp_valid & framing_enabled & dp_extended_framing & dp_revision;
    process(clk100)
    begin
        if rising_edge(clk100) then
            if capabilities_invalid='1' then
                sink_status_valid<='0'; sink_status_receiving<='0'; sink_status_next<=0;
            elsif status_de='1' then
                if aux_addr=x"00" then
                    sink_status_pending(7 downto 0)<=aux_data;
                    sink_status_next<=1; sink_status_receiving<='1';
                elsif sink_status_receiving='1' and unsigned(aux_addr)=sink_status_next then
                    if sink_status_next=7 then
                        sink_status_bytes<=aux_data & sink_status_pending(55 downto 0);
                        sink_status_seq<=sink_status_seq+1; sink_status_valid<='1';
                        sink_status_receiving<='0'; sink_status_next<=0;
                    else
                        sink_status_pending(sink_status_next*8+7 downto sink_status_next*8)<=aux_data;
                        sink_status_next<=sink_status_next+1;
                    end if;
                else
                    sink_status_receiving<='0';
                end if;
            end if;
        end if;
    end process;
    diagnostic_status(47 downto 0) <= aux_diagnostic;
    diagnostic_status(63 downto 48) <= capabilities_invalid & dp_link_rate_2_70 & dp_coding_supported(0) & support_RGB444 &
        tx_running(1) & tx_running(0) & align_locked & symbol_locked & equ_locked & clock_locked &
        active_tx_ready & tx_powerup & sink_capable & dp_valid & edid_valid & hpd_present;
    -- The per-lane reset controllers and this manager share clk100.
    active_tx_ready <= tx_running(0) when active_channel_count="001" else
                       (tx_running(0) and tx_running(1)) when active_channel_count="010" else
                       (tx_running(0) and tx_running(1) and tx_running(2) and tx_running(3)) when active_channel_count="100" else '0';
    capabilities_invalid <= (not hpd_present) or aux_capabilities_reset;
    -- Qualified target: a two-lane HBR RGB link. Complete capabilities
    -- must precede main-link power or training; mode acceptance is separate.
    sink_capable <= '1' when edid_valid='1' and support_RGB444='1'
                   and dp_valid='1' and dp_link_rate_2_70='1'
                   and dp_coding_supported(0)='1'
                   and (not enhanced_mode or dp_extended_framing='1')
                   and unsigned(dp_training_interval)<=4
                   and (dp_link_count=x"2" or dp_link_count=x"4") else '0';
    -- Feed the number of links from the registers into the link management logic
    sink_channel_count <= dp_link_count(2 downto 0);
    tx_preemp_0p0 <= preemp_0p0_i;
    tx_preemp_3p5 <= preemp_3p5_i;
    tx_preemp_6p0 <= preemp_6p0_i;

    tx_swing_0p4 <= swing_0p4_i;
    tx_swing_0p6 <= swing_0p6_i;
    tx_swing_0p8 <= swing_0p8_i;

i_hotplug_decode: hotplug_decode port map (
        clk     => clk100,
        hpd     => hpd,
        irq     => hpd_irq,
        present => hpd_present);

i_aux_channel: aux_channel generic map(enable_link_probe=>enable_link_probe, link_poll_cycles=>link_poll_cycles,
    enable_ser=>enable_ser,ser_cycles_per_ms=>ser_cycles_per_ms,disable_scrambling=>disable_scrambling,
    initialize_sink_power=>initialize_sink_power) port map (
           sink_power_status=>sink_power_status,
           ser_arm=>ser_arm,ser_stream_ready=>ser_stream_ready,ser_diagnostic=>ser_diagnostic,
           probe_errors=>probe_errors, probe_time=>probe_time, probe_config=>probe_config, probe_metadata=>probe_metadata,
           clk             => clk100,
           debug_pmod      => debug,
           diagnostic_status => aux_diagnostic,
           ------------------------------
         edid_de         => edid_de,
         dp_reg_de       => dp_reg_de,
         adjust_de       => adjust_de,
         status_de       => status_de,
         aux_addr        => aux_addr,
           aux_data        => aux_data,
           ------------------------------
           link_count      => active_channel_count,
           enhanced_framing => framing_enabled,
         hpd_irq         => hpd_irq,
         hpd_present     => hpd_present,
         sink_capable    => sink_capable,
         tx_ready        => active_tx_ready,
         training_interval => dp_training_interval,
         dpcd_revision     => dp_revision,
         capabilities_reset => aux_capabilities_reset,
           ------------------------------
         preemp_0p0      => preemp_0p0_i,
         preemp_3p5      => preemp_3p5_i,
         preemp_6p0      => preemp_6p0_i,
         swing_0p4       => swing_0p4_i,
         swing_0p6       => swing_0p6_i,
         swing_0p8       => swing_0p8_i,

         clock_locked    => clock_locked,
         equ_locked      => equ_locked,
         symbol_locked   => symbol_locked,
         align_locked    => align_locked,

           ------------------------------
         tx_powerup          => tx_powerup,
         tx_clock_train      => tx_clock_train,
         tx_align_train      => tx_align_train,
         tx_link_established => tx_link_established,
           ------------------------------
             dp_tx_hp_detect => hpd,
           dp_tx_aux_p     => aux_tx_p,
           dp_tx_aux_n     => aux_tx_n,
           dp_rx_aux_p     => aux_rx_p,
           dp_rx_aux_n     => aux_rx_n
        );


i_edid_observer: entity work.edid_observer port map (
    clk=>clk100, de=>edid_de, invalidate=>capabilities_invalid, decoded_valid=>edid_valid,
    addr=>aux_addr, data=>aux_data, block_data=>edid_block_diagnostic, metadata=>edid_metadata_diagnostic);

i_edid_decode: edid_decode port map (
           clk              => clk100,
           edid_de          => edid_de,
           edid_addr        => aux_addr,
           edid_data        => aux_data,
           invalidate       => capabilities_invalid,

           valid            => edid_valid,

           support_RGB444   => support_RGB444,
           support_YCC444   => support_YCC444,
           support_YCC422   => support_YCC422,

           pixel_clock_x10k => pixel_clock_x10k,

           h_visible_len    => h_visible_len,
           h_blank_len      => h_blank_len,
           h_front_len      => h_front_len,
           h_sync_len       => h_sync_len,

           v_visible_len    => v_visible_len,
           v_blank_len      => v_blank_len,
           v_front_len      => v_front_len,
           v_sync_len       => v_sync_len,
           interlaced       => interlaced);

i_dp_reg_decode: dp_register_decode port map (
            clk                => clk100,
            de                 => dp_reg_de,
            addr               => aux_addr,
            data               => aux_data,
            invalidate         => capabilities_invalid,
            valid              => dp_valid,

            revision           => dp_revision,
            link_rate_2_70     => dp_link_rate_2_70,
            link_rate_1_62     => dp_link_rate_1_62,
            extended_framing   => dp_extended_framing,
            link_count         => dp_link_count,
            max_downspread     => dp_max_downspread,
            coding_supported   => dp_coding_supported,
            port0_capabilities => dp_port0_capabilities,
            port1_capabilities => dp_port1_capabilities,
            norp               => dp_norp,
            training_interval  => dp_training_interval
       );

i_link_signal_mgmt:  link_signal_mgmt Port map (
        mgmt_clk             => clk100,

        tx_powerup           => tx_powerup,

        status_de            => status_de,
        adjust_de            => adjust_de,
        addr                 => aux_addr,
        data                 => aux_data,

        sink_channel_count   => sink_channel_count,
        source_channel_count => source_channel_count,
        active_channel_count => active_channel_count,
        stream_channel_count => stream_channel_count,

        powerup_channel      => tx_powerup_channel,

        clock_locked         => clock_locked,
        equ_locked           => equ_locked,
        symbol_locked        => symbol_locked,
        align_locked         => align_locked,

        preemp_0p0           => preemp_0p0_i,
        preemp_3p5           => preemp_3p5_i,
        preemp_6p0           => preemp_6p0_i,

        swing_0p4            => swing_0p4_i,
        swing_0p6            => swing_0p6_i,
        swing_0p8            => swing_0p8_i);
end Behavioral;
