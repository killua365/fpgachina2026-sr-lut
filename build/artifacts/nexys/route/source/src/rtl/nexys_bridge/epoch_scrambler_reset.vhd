-- Start each established-link epoch with a visible SR, then repeat every
-- 512 BS boundaries. Training overrides can hide the free-running SR; the
-- established-link pulse arrives after AUX exits training and rewrites lanes.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity epoch_scrambler_reset is
    port(clk : in std_logic; restart_epoch : in std_logic;
         in_data : in std_logic_vector(71 downto 0);
         out_data : out std_logic_vector(71 downto 0));
end entity;
architecture rtl of epoch_scrambler_reset is
    signal count,next_count : unsigned(8 downto 0):=(others=>'0');
    signal pending,next_pending : std_logic:='0';
    constant BS:std_logic_vector(8 downto 0):='1' & x"BC";
    constant SR:std_logic_vector(8 downto 0):='1' & x"1C";
begin
    process(in_data,count,pending,restart_epoch)
        variable n:unsigned(8 downto 0);
        variable force_reset:std_logic;
        variable result:std_logic_vector(71 downto 0);
    begin
        n:=count; force_reset:=pending or restart_epoch; result:=in_data;
        for slot in 0 to 1 loop
            if in_data(slot*9+8 downto slot*9)=BS then
                if force_reset='1' or n=511 then
                    for lane in 0 to 3 loop result(lane*18+slot*9+8 downto lane*18+slot*9):=SR; end loop;
                    n:=(others=>'0'); force_reset:='0';
                else n:=n+1;
                end if;
            end if;
        end loop;
        out_data<=result; next_count<=n; next_pending<=force_reset;
    end process;
    process(clk)
    begin
        if rising_edge(clk) then count<=next_count; pending<=next_pending; end if;
    end process;
end architecture;
