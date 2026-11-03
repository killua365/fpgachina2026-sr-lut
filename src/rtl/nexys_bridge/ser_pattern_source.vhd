-- DP 1.1a 2.5.3.5.2: unscrambled SER source, before SR/framing/scrambler.
-- Every data symbol, INCLUDING VB-ID/Mvid/Maud, is zero. One BS per 8192
-- symbols. This is a link diagnostic source, not a video/idle source.
-- Board use additionally requires qualified mode writes and read-clear timing.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity ser_pattern_source is
    port(clk : in std_logic; data : out std_logic_vector(71 downto 0):=(others=>'0'));
end entity;
architecture rtl of ser_pattern_source is
    signal cycle : unsigned(11 downto 0):=(others=>'0');
    constant BS : std_logic_vector(8 downto 0):='1' & x"BC";
begin
    process(clk)
    begin
        if rising_edge(clk) then
            data<=(others=>'0');
            if cycle=0 then
                for lane in 0 to 3 loop data(lane*18+8 downto lane*18)<=BS; end loop;
            end if;
            cycle<=cycle+1;
        end if;
    end process;
end architecture;
