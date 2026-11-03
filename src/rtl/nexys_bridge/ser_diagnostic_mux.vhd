-- SER-only UART schedule. Six consecutive pages capture one atomic sample.
-- Normal-video diagnostic mux and its page timing remain unchanged.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity ser_diagnostic_mux is
    generic(interval_cycles : positive := 5000000);
    port(clk : in std_logic;
         aux_status,sink_status,config,metadata : in std_logic_vector(63 downto 0);
         ser_sample : in std_logic_vector(383 downto 0);
         kind : out std_logic_vector(7 downto 0);
         payload : out std_logic_vector(63 downto 0));
end entity;
architecture rtl of ser_diagnostic_mux is
    signal count : natural range 0 to interval_cycles-1 := 0;
    signal page : natural range 0 to 9 := 0;
    signal saved_config,saved_metadata : std_logic_vector(63 downto 0):=(others=>'0');
    signal saved_sample : std_logic_vector(383 downto 0):=(others=>'0');
begin
    process(clk)
    begin
        if rising_edge(clk) then
            if count=interval_cycles-1 then
                count<=0;
                if page=1 then saved_config<=config; saved_metadata<=metadata; end if;
                if page=3 then saved_sample<=ser_sample; end if;
                if page=9 then page<=0; else page<=page+1; end if;
            else count<=count+1; end if;
        end if;
    end process;
    process(page,aux_status,sink_status,saved_config,saved_metadata,saved_sample)
    begin
        case page is
            when 0=>kind<=x"01"; payload<=aux_status;
            when 1=>kind<=x"05"; payload<=sink_status;
            when 2=>kind<=x"19"; payload<=saved_config;
            when 3=>kind<=x"1A"; payload<=saved_metadata;
            when others=>
                kind<=std_logic_vector(to_unsigned(27+page-4,8));
                payload<=saved_sample((page-3)*64-1 downto (page-4)*64);
        end case;
    end process;
end architecture;
