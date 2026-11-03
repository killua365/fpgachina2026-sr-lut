-- 2 ms UART slots. Every 25th slot retains the original 50 ms diagnostics.
-- Other slots repeat the three frozen captures, with an explicit address.
-- kind 34: version=1, address_bits, completion flags (never data).
-- kind 35..37: payload[39:0]=raw symbols; [55:40]=word address;
-- [63:56]=version 1. No partial bank is published. One configuration/epoch.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity stream_capture_mux is
    generic(slot_cycles:positive:=200000;address_bits:positive range 4 to 12:=12);
    port(clk:in std_logic;normal_kind:in std_logic_vector(7 downto 0);
         normal_payload:in std_logic_vector(63 downto 0);
         complete:in std_logic_vector(2 downto 0);
         read_data:in std_logic_vector(39 downto 0);
         read_address:out unsigned(address_bits-1 downto 0);
         read_bank:out unsigned(1 downto 0);
         kind:out std_logic_vector(7 downto 0);
         payload:out std_logic_vector(63 downto 0));
end;
architecture rtl of stream_capture_mux is
    signal ticks:natural range 0 to slot_cycles-1:=0;
    signal slot:natural range 0 to 24:=0;
    signal bank:natural range 0 to 2:=0;
    signal address:unsigned(address_bits-1 downto 0):=(others=>'0');
begin
    assert slot_cycles>=8 report "RAM read settling time required" severity failure;
    read_address<=address;read_bank<=to_unsigned(bank,2);
    process(all)
    begin
        if slot=24 then kind<=normal_kind;payload<=normal_payload;
        elsif complete(bank)='1' then
            kind<=std_logic_vector(to_unsigned(35+bank,8));
            payload<=x"01" & std_logic_vector(resize(address,16)) & read_data;
        else
            kind<=x"22";
            payload<=(others=>'0');payload(7 downto 0)<=x"01";
            payload(15 downto 8)<=std_logic_vector(to_unsigned(address_bits,8));
            payload(18 downto 16)<=complete;
        end if;
    end process;
    process(clk)
    begin
        if rising_edge(clk) then
            if ticks=slot_cycles-1 then
                ticks<=0;
                if slot=24 then slot<=0;
                else
                    slot<=slot+1;
                    if complete(bank)='1' then
                        if address=(address'range=>'1') then
                            address<=(others=>'0');
                            if bank=2 then bank<=0;else bank<=bank+1;end if;
                        else address<=address+1;end if;
                    else
                        -- A missing trigger cannot block the other banks.
                        address<=(others=>'0');
                        if bank=2 then bank<=0;else bank<=bank+1;end if;
                    end if;
                end if;
            else ticks<=ticks+1;end if;
        end if;
    end process;
end;
