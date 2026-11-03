----------------------------------------------------------------------------------
-- Engineer: Mike Field <hamster@snap.net.nz<
--
-- Module Name: dp_aux_messages - Behavioral
--
-- Description: Messages that will be sent over thr DisplayPort AUX interface
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
------------------------------------------------------------------------------------

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity dp_aux_messages is
   port ( clk         : in std_logic;

         -- Interface to send messages
         msg_de      : in std_logic;
         msg         : in std_logic_vector(7 downto 0);
         busy        : out std_logic;

         --- Interface to the AUX Channel
         aux_tx_wr_en : out std_logic;
         aux_tx_data  : out std_logic_vector(7 downto 0);
         enhanced_framing : in std_logic := '0');
end dp_aux_messages;

architecture arch of dp_aux_messages is
    signal counter : unsigned(11 downto 0) := (others => '0');
begin

process(clk)
   begin
      if rising_edge(clk) then
         case counter is
            -- Write to I2C device at x50 (EDID)
            when x"010" => aux_tx_data <= x"40"; aux_tx_wr_en <= '1';
            when x"011" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"012" => aux_tx_data <= x"50"; aux_tx_wr_en <= '1';
            when x"013" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"014" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';

            -- Read a block of EDID data
            when x"020" => aux_tx_data <= x"50"; aux_tx_wr_en <= '1';
            when x"021" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"022" => aux_tx_data <= x"50"; aux_tx_wr_en <= '1';
            when x"023" => aux_tx_data <= x"0F"; aux_tx_wr_en <= '1';

            -- I2C WRITE_STATUS_UPDATE, MOT=1, address-only (no length/data).
            when x"120" => aux_tx_data <= x"60"; aux_tx_wr_en <= '1';
            when x"121" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"122" => aux_tx_data <= x"50"; aux_tx_wr_en <= '1';

            -- End/abort EDID transaction: address-only READ with MOT=0.
            when x"130" => aux_tx_data <= x"10"; aux_tx_wr_en <= '1';
            when x"131" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"132" => aux_tx_data <= x"50"; aux_tx_wr_en <= '1';

            -- Read Sink count
            when x"030" => aux_tx_data <= x"90"; aux_tx_wr_en <= '1';
            when x"031" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"032" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"033" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';

            -- Read all 16 base capability bytes, including AUX_RD_INTERVAL.
            when x"040" => aux_tx_data <= x"90"; aux_tx_wr_en <= '1';
            when x"041" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"042" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"043" => aux_tx_data <= x"0F"; aux_tx_wr_en <= '1';

            -- Write DPCD powerstate D3
            when x"050" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"051" => aux_tx_data <= x"06"; aux_tx_wr_en <= '1';
            when x"052" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"053" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"054" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';

            -- Opt-in sink power experiment. Keep historical D3 message 05
            -- untouched; initialization never calls it. DP 1.1a p137.
            when x"1B0" | x"1D0" => aux_tx_data<=x"90";aux_tx_wr_en<='1';
            when x"1B1" | x"1D1" => aux_tx_data<=x"06";aux_tx_wr_en<='1';
            when x"1B2" | x"1D2" | x"1B3" | x"1D3" => aux_tx_data<=x"00";aux_tx_wr_en<='1';
            when x"1C0" => aux_tx_data<=x"80";aux_tx_wr_en<='1';
            when x"1C1" => aux_tx_data<=x"06";aux_tx_wr_en<='1';
            when x"1C2" | x"1C3" => aux_tx_data<=x"00";aux_tx_wr_en<='1';
            when x"1C4" => aux_tx_data<=x"01";aux_tx_wr_en<='1';

            -- Set channel coding (8b/10b)
            when x"060" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"061" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"062" => aux_tx_data <= x"08"; aux_tx_wr_en <= '1';
            when x"063" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"064" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';

            -- Set link bandwidth 2.70 Gb/s
            when x"070" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"071" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"072" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"073" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"074" => aux_tx_data <= x"0A"; aux_tx_wr_en <= '1';

            -- Write Link Downspread
            when x"080" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"081" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"082" => aux_tx_data <= x"07"; aux_tx_wr_en <= '1';
            when x"083" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            -- Fixed 135 MHz GTP source: no downspread is generated.
            when x"084" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';

            -- Set link count 1
            when x"090" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"091" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"092" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"093" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"094" => aux_tx_data <= enhanced_framing & "0000001"; aux_tx_wr_en <= '1';  -- Standard framing, one channel

            -- Set link count 2
            when x"0A0" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"0A1" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"0A2" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"0A3" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"0A4" => aux_tx_data <= enhanced_framing & "0000010"; aux_tx_wr_en <= '1';

            -- Set link count 4
            when x"0B0" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"0B1" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"0B2" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"0B3" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"0B4" => aux_tx_data <= enhanced_framing & "0000100"; aux_tx_wr_en <= '1';

            -- Set training pattern 1
            when x"0C0" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"0C1" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"0C2" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"0C3" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"0C4" => aux_tx_data <= x"21"; aux_tx_wr_en <= '1';

            -- Read link status for all four lanes
            when x"0D0" => aux_tx_data <= x"90"; aux_tx_wr_en <= '1';
            when x"0D1" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"0D2" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"0D3" => aux_tx_data <= x"07"; aux_tx_wr_en <= '1';

            --  Read the Adjust_Request registers
            when x"0E0" => aux_tx_data <= x"90"; aux_tx_wr_en <= '1';
            when x"0E1" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"0E2" => aux_tx_data <= x"06"; aux_tx_wr_en <= '1';
            when x"0E3" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';

            -- Set training pattern 2
            when x"0F0" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"0F1" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"0F2" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"0F3" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"0F4" => aux_tx_data <= x"22"; aux_tx_wr_en <= '1';

            -- Resd lane align status for all four lanes
            when x"100" => aux_tx_data <= x"90"; aux_tx_wr_en <= '1';
            when x"101" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"102" => aux_tx_data <= x"04"; aux_tx_wr_en <= '1';
            when x"103" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';

            -- Turn off training patterns / Switch to normal
            when x"110" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"111" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"112" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"113" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"114" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';  -- Scrambler enabled

            -- Dedicated DP 1.1 SER quality pattern, not normal video.
            when x"190" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"191" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"192" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"193" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"194" => aux_tx_data <= x"08"; aux_tx_wr_en <= '1';

            -- Separate video diagnostic: normal stream, scrambling disabled.
            when x"1A0" => aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"1A1" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"1A2" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"1A3" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"1A4" => aux_tx_data <= x"20"; aux_tx_wr_en <= '1';

            -- Optional post-training diagnostic reads; neither writes the sink.
            when x"150" | x"170" => aux_tx_data <= x"90"; aux_tx_wr_en <= '1';
            when x"151" => aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"152" => aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"153" => aux_tx_data <= x"08"; aux_tx_wr_en <= '1'; -- 0x100..108, 9 bytes
            when x"171" => aux_tx_data <= x"02"; aux_tx_wr_en <= '1';
            when x"172" => aux_tx_data <= x"10"; aux_tx_wr_en <= '1';
            when x"173" => aux_tx_data <= x"03"; aux_tx_wr_en <= '1'; -- 0x210..213, 4 bytes, read-clear

            -- TRAINING_LANE0..3_SET: source supports levels 0..2.
            -- Flags describe the implemented joint limit; 0x38 clamps to V2/P1.
            when x"140" | x"160" | x"180" | x"240" | x"260" | x"280" | x"340" | x"360" | x"380" =>
                aux_tx_data <= x"80"; aux_tx_wr_en <= '1';
            when x"141" | x"161" | x"181" | x"241" | x"261" | x"281" | x"341" | x"361" | x"381" =>
                aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"142" | x"162" | x"182" | x"242" | x"262" | x"282" | x"342" | x"362" | x"382" =>
                aux_tx_data <= x"03"; aux_tx_wr_en <= '1';
            when x"143" | x"163" | x"183" | x"243" | x"263" | x"283" | x"343" | x"363" | x"383" =>
                aux_tx_data <= x"03"; aux_tx_wr_en <= '1';
            when x"144" | x"145" | x"146" | x"147" =>
                aux_tx_data <= x"00"; aux_tx_wr_en <= '1';
            when x"164" | x"165" | x"166" | x"167" =>
                aux_tx_data <= x"01"; aux_tx_wr_en <= '1';
            when x"184" | x"185" | x"186" | x"187" =>
                aux_tx_data <= x"06"; aux_tx_wr_en <= '1';
            when x"244" | x"245" | x"246" | x"247" =>
                aux_tx_data <= x"08"; aux_tx_wr_en <= '1';
            when x"264" | x"265" | x"266" | x"267" =>
                aux_tx_data <= x"09"; aux_tx_wr_en <= '1';
            when x"284" | x"285" | x"286" | x"287" =>
                aux_tx_data <= x"2E"; aux_tx_wr_en <= '1';
            when x"344" | x"345" | x"346" | x"347" =>
                aux_tx_data <= x"30"; aux_tx_wr_en <= '1';
            when x"364" | x"365" | x"366" | x"367" =>
                aux_tx_data <= x"35"; aux_tx_wr_en <= '1';
            when x"384" | x"385" | x"386" | x"387" =>
                aux_tx_data <= x"2E"; aux_tx_wr_en <= '1';
            when others => aux_tx_data <= x"00"; aux_tx_wr_en <= '0';
         end case;

         ----------------------------
         -- Move on to the next word?
         ----------------------------
         if counter(3 downto 0) = x"F" then
            busy <= '0';
         else
            counter <= counter+1;
         end if;

         ----------------------------------------
         -- Are we being asked to send a message?
         --
         -- But only do it of we are not already
         -- sending something!
         ----------------------------------------
         -- Controller wait states issue message 00 (no transaction). Do not
         -- occupy the ROM for 16 cycles: an already-ready GTP can enter TP1
         -- a few cycles later, and its real request must not be discarded.
         if msg_de = '1' and msg /= x"00" and counter(3 downto 0) = x"F" then
            counter <= unsigned(msg & x"0");
            busy <= '1';
         end if;

      end if;
   end process;

end architecture;
