-- Expand a two-lane Default BS/SR header to the DP Enhanced Framing header.
-- The three extra K symbols consume three otherwise-zero blanking symbols;
-- video/TU/MSA scheduling and line/frame periods therefore remain unchanged.
-- Input contract: aligned BS/SR, two VB-ID/Mvid/Maud triples on each lane,
-- followed by at least three zero data symbols. Both slot alignments work.
library ieee;
use ieee.std_logic_1164.all;
entity enhanced_framing is
    port(clk : in std_logic;
         in_data : in std_logic_vector(71 downto 0);
         out_data : out std_logic_vector(71 downto 0) := (others=>'0'));
end;
architecture rtl of enhanced_framing is
    subtype symbol is std_logic_vector(8 downto 0);
    type symbol_array is array(0 to 5) of symbol;
    type lane_headers is array(0 to 3) of symbol_array;
    signal headers : lane_headers := (others=>(others=>(others=>'0')));
    signal phase : natural range 0 to 9 := 0;
    signal marker : symbol := '1'&x"BC";
    constant BS : symbol := '1'&x"BC";
    constant SR : symbol := '1'&x"1C";
    constant BF : symbol := '1'&x"7C"; -- K28.3
begin
    process(clk)
        variable p : natural range 0 to 9;
        variable m, incoming, outgoing : symbol;
        variable h : lane_headers;
        variable result : std_logic_vector(71 downto 0);
    begin
        if rising_edge(clk) then
            p:=phase; m:=marker; h:=headers; result:=in_data;
            for slot in 0 to 1 loop
                incoming:=in_data(slot*9+8 downto slot*9);
                if p=0 then
                    if incoming=BS or incoming=SR then
                        m:=incoming; p:=1;
                        -- synthesis translate_off
                        for lane in 1 to 1 loop
                            assert in_data(lane*18+slot*9+8 downto lane*18+slot*9)=incoming
                                report "Enhanced framing requires aligned lane markers" severity failure;
                        end loop;
                        -- synthesis translate_on
                    end if;
                else
                    for lane in 0 to 3 loop
                        incoming:=in_data(lane*18+slot*9+8 downto lane*18+slot*9);
                        if p<=6 then h(lane)(p-1):=incoming; end if;
                        if p=1 or p=2 then outgoing:=BF;
                        elsif p=3 then outgoing:=m;
                        else outgoing:=h(lane)(p-4); end if;
                        -- synthesis translate_off
                        if lane<2 and p>=7 then
                            assert incoming=('0'&x"00")
                                report "Enhanced framing would discard nonzero blanking data" severity failure;
                        end if;
                        -- synthesis translate_on
                        result(lane*18+slot*9+8 downto lane*18+slot*9):=outgoing;
                    end loop;
                    if p=9 then p:=0; else p:=p+1; end if;
                end if;
            end loop;
            phase<=p; marker<=m; headers<=h; out_data<=result;
        end if;
    end process;
end;
