-- Passive copy of the EDID base block accepted by the existing decoder.
-- No signal feeds back into AUX, training or video generation.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity edid_observer is
    port(clk, de, invalidate, decoded_valid : in std_logic;
         addr, data : in std_logic_vector(7 downto 0);
         block_data : out std_logic_vector(1023 downto 0) := (others=>'0');
         metadata : out std_logic_vector(63 downto 0));
end;
architecture rtl of edid_observer is
    signal pending : std_logic_vector(1023 downto 0) := (others=>'0');
    signal previous_valid, published_valid : std_logic := '0';
    signal generation : unsigned(31 downto 0) := (others=>'0');
begin
    -- v1: generation[47:16], version[15:8], valid[0]; all other bits reserved.
    metadata <= x"0000" & std_logic_vector(generation) & x"01" & "0000000" & published_valid;
    process(clk)
        variable index_value : natural;
    begin
        if rising_edge(clk) then
            previous_valid<=decoded_valid;
            if de='1' and unsigned(addr)<128 then
                index_value:=to_integer(unsigned(addr));
                pending(index_value*8+7 downto index_value*8)<=data;
            end if;
            if invalidate='1' then
                previous_valid<='0'; published_valid<='0';
                if published_valid='1' then generation<=generation+1; end if;
            elsif decoded_valid='1' and previous_valid='0' then
                -- Decoder publishes valid one clock after accepting byte 127.
                -- At this edge pending already contains the complete block.
                block_data<=pending; published_valid<='1'; generation<=generation+1;
            elsif decoded_valid='0' and published_valid='1' then
                published_valid<='0'; generation<=generation+1;
            end if;
        end if;
    end process;
end;
