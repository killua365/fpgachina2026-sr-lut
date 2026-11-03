----------------------------------------------------------------------------------
-- Module Name: main_stream_processing - Behavioral
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
--  0.1 | 2015-10-15 | Initial Version
------------------------------------------------------------------------------------
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity main_stream_processing is
    generic( use_hw_8b10b_support : std_logic := '0'; enhanced_mode : boolean := false;
             two_lane_stream : boolean := false; ser_mode : boolean := false;
             disable_scrambling : boolean := false);
    Port ( symbol_clk          : in  STD_LOGIC;
           tx_link_established : in  STD_LOGIC;
           source_ready        : in  STD_LOGIC;
           tx_clock_train      : in  STD_LOGIC;
           tx_align_train      : in  STD_LOGIC;
           in_data             : in  STD_LOGIC_VECTOR (72 downto 0);
           tx_symbols          : out STD_LOGIC_VECTOR (79 downto 0);
           ser_arm             : in std_logic := '0';
           ser_ready           : out std_logic := '0');
end main_stream_processing;

architecture Behavioral of main_stream_processing is
    component ser_pattern_source is
        port(clk : in std_logic; data : out std_logic_vector(71 downto 0));
    end component;
    component idle_pattern_inserter is
        generic(two_lane_headers : boolean := false);
        port ( 
            clk              : in  std_logic;
            channel_ready    : in  std_logic;
            source_ready     : in  std_logic;
            in_data          : in  std_logic_vector(72 downto 0);
            out_data         : out std_logic_vector(71 downto 0);
            channel_ready_sync : out std_logic := '0'
        );
    end component;    
    
    component epoch_scrambler_reset is
        port ( 
            clk      : in  std_logic;
            restart_epoch : in std_logic;
            in_data  : in  std_logic_vector(71 downto 0);
            out_data : out std_logic_vector(71 downto 0)
        );
    end component;
    
    component scrambler is
        port ( 
            clk        : in  std_logic;
            bypass0    : in  std_logic;
            bypass1    : in  std_logic; 
            in_data    : in  std_logic_vector(17 downto 0);
            out_data   : out std_logic_vector(17 downto 0)
        );
    end component;

    component scrambler_all_channels is
        port ( 
            clk        : in  std_logic;
            bypass0    : in  std_logic;
            bypass1    : in  std_logic; 
            in_data    : in  std_logic_vector(71 downto 0);
            out_data   : out std_logic_vector(71 downto 0)
        );
    end component;

    component insert_training_pattern is
    port (
        clk                : in  std_logic;
        clock_train        : in  std_logic;
        align_train        : in  std_logic;

        in_data            : in  std_logic_vector(71 downto 0);
        out_data           : out std_logic_vector(79 downto 0)
        );
    end component;

    component skew_channels is
        port ( 
            clk      : in  std_logic;
            in_data  : in  std_logic_vector(79 downto 0);
            out_data : out std_logic_vector(79 downto 0)
        );
    end component;
    
    
    component data_to_8b10b is
        port ( 
            clk      : in  std_logic;
            in_data  : in  std_logic_vector(19 downto 0);
            out_data : out std_logic_vector(19 downto 0)
        );
    end component;

    signal signal_data         : std_logic_vector(71 downto 0) := (others => '0');
    signal idle_data, ser_data : std_logic_vector(71 downto 0);
    signal ser_arm_meta,ser_arm_sync : std_logic := '0';
    attribute ASYNC_REG : string;
    attribute SHREG_EXTRACT : string;
    attribute ASYNC_REG of ser_arm_meta,ser_arm_sync : signal is "TRUE";
    attribute SHREG_EXTRACT of ser_arm_meta,ser_arm_sync : signal is "NO";
    signal ser_guard : natural range 0 to 127 := 0;
    signal ser_acquired : std_logic := '0';
    signal sr_inserted_data    : std_logic_vector(71 downto 0) := (others => '0');    
    signal framed_data : std_logic_vector(71 downto 0);
    signal scrambled_data      : std_logic_vector(71 downto 0) := (others => '0');
    signal before_skew         : std_logic_vector(79 downto 0) := (others => '0');
    signal final_data          : std_logic_vector(79 downto 0) := (others => '0');
    signal link_sync,link_previous : std_logic := '0';
    signal startup_count : unsigned(4 downto 0) := (others=>'0');
    signal restart_epoch : std_logic;
    signal bypass_level : std_logic;
    constant delay_index : std_logic_vector(7 downto 0) := "11100100"; -- 3,2,1,0 for use as a lookup table in the generate loop

begin

assert not (ser_mode and disable_scrambling) report "SER requires scrambling" severity failure;
bypass_level <= '1' when disable_scrambling else '0';

process(symbol_clk)
begin
    if rising_edge(symbol_clk) then
        if startup_count/=31 then startup_count<=startup_count+1; end if;
        -- The training selector starts with a short D10.2 drain even when its
        -- inputs are low. Do not expose half an enhanced SR at power-up when
        -- a testbench or caller starts with an already-established link.
        if startup_count=31 then link_previous<=link_sync;
        else link_previous<='0'; end if;
    end if;
end process;
restart_epoch <= link_sync and not link_previous when startup_count=31 and not ser_mode else '0';
signal_data <= ser_data when ser_mode else idle_data;
g_ser: if ser_mode generate
    pattern: ser_pattern_source port map(clk=>symbol_clk,data=>ser_data);
    -- The AUX controller arms only after deasserting training. Allow its
    -- synchronizers/pipeline to drain, then observe a complete lane-1 SR
    -- at the actual pre-GTP output (including the lane skew).
    process(symbol_clk)
    begin
        if rising_edge(symbol_clk) then
            ser_arm_meta<=ser_arm; ser_arm_sync<=ser_arm_meta;
            if ser_arm_sync='0' then ser_guard<=0; ser_acquired<='0';
            elsif ser_guard/=127 then ser_guard<=ser_guard+1;
            elsif (enhanced_mode and final_data(38 downto 30)=('1' & x"1C")) or
                  (not enhanced_mode and (final_data(28 downto 20)=('1' & x"1C") or
                                           final_data(38 downto 30)=('1' & x"1C"))) then
                ser_acquired<='1';
            end if;
        end if;
    end process;
    -- Only the registered level crosses back to management clock. The
    -- process clears it on disarm; a combinational AND here can glitch
    -- before the destination synchronizer and triggers CDC-10.
    ser_ready<=ser_acquired;
end generate;
g_not_ser: if not ser_mode generate
    ser_data<=(others=>'0'); ser_ready<='0';
end generate;

-- Lane count determines VB-ID/Mvid/Maud repetition independently of framing.
-- Preserve the historical enhanced two-lane setting for existing instantiations.
i_idle_pattern_inserter: idle_pattern_inserter generic map(two_lane_headers=>enhanced_mode or two_lane_stream) port map (
            clk              => symbol_clk,
            channel_ready    => tx_link_established,
            source_ready     => source_ready,
            
            in_data          => in_data,
            out_data         => idle_data,
            channel_ready_sync => link_sync
        );

i_scrambler_reset_inserter: epoch_scrambler_reset
        port map ( 
            clk       => symbol_clk,
            restart_epoch => restart_epoch,
            in_data   => signal_data,
            out_data  => sr_inserted_data
        );

g_enhanced: if enhanced_mode generate
    encoder: entity work.enhanced_framing port map(clk=>symbol_clk,in_data=>sr_inserted_data,out_data=>framed_data);
end generate;
g_default: if not enhanced_mode generate
    framed_data<=sr_inserted_data;
end generate;

i_scrambler:  scrambler_all_channels
        port map ( 
            clk        => symbol_clk,
            bypass0    => bypass_level,
            bypass1    => bypass_level,
            in_data    => framed_data,
            out_data   => scrambled_data
        );

i_insert_training_pattern: insert_training_pattern port map (
        clk               => symbol_clk,
        clock_train       => tx_clock_train,
        align_train       => tx_align_train, 
        -- Adds one bit per symbol - the force_neg parity flag         
        in_data           => scrambled_data,
        out_data          => before_skew
    );

i_skew_channels: skew_channels port map (
        clk               => symbol_clk,
        in_data           => before_skew,
        out_data          => final_data
    );

g_per_channel: for i in 0 to 3 generate  -- lnk_j8_lane_p'high
   ----------------------------------------------
   -- Soft 8b/10b encoder
   ----------------------------------------------
g2: if use_hw_8b10b_support = '0' generate
i_data_to_8b10b: data_to_8b10b port map ( 
        clk      => symbol_clk,
        in_data  => final_data(19+i*20 downto 0+i*20),
        out_data => tx_symbols(19+i*20 downto 0+i*20));
    end generate;
    
g3: if use_hw_8b10b_support = '1' generate
      tx_symbols <= final_data;
    end generate;
    end generate;  --- For FOR GENERATE loop

end Behavioral;
