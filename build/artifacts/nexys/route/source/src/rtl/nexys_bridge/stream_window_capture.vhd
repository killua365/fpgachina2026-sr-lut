-- One-shot passive pre-GTP capture. No output feeds video, training or resets.
-- Three independent immutable banks: first SS, subsequent active-line SR,
-- first BE after SS. Publish only after the last write, never overwrite.
-- Dual-clock block RAM provides the data crossing; independent monotonic
-- completion flags cross with XPM. An unfinished bank must not be read.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library xpm;
use xpm.vcomponents.all;
entity stream_window_capture is
    generic(address_bits:positive range 4 to 12:=12);
    port(symbol_clk,mgmt_clk:in std_logic;
         symbols:in std_logic_vector(39 downto 0);
         read_address:in unsigned(address_bits-1 downto 0);
         read_bank:in unsigned(1 downto 0);
         read_data:out std_logic_vector(39 downto 0);
         complete:out std_logic_vector(2 downto 0));
end;
architecture rtl of stream_window_capture is
    constant depth:positive:=2**address_bits;
    type counts is array(0 to 2) of natural range 0 to depth-1;
    type words is array(0 to 2) of std_logic_vector(39 downto 0);
    signal positions:counts:=(others=>0);
    signal active,done,done_mgmt:std_logic_vector(2 downto 0):="000";
    signal readable1,readable2:std_logic_vector(2 downto 0):="000";
    signal bank_data:words;
    signal triggers:std_logic_vector(2 downto 0);
    signal msa_seen:std_logic:='0';
    signal be_age:natural range 0 to 4095:=4095;
    function contains(value:std_logic_vector(39 downto 0); k:natural) return boolean is
    begin
        return unsigned(value(9 downto 0))=k or unsigned(value(19 downto 10))=k;
    end;
begin
    triggers(0)<='1' when contains(symbols,16#15C#) else '0';
    triggers(1)<='1' when msa_seen='1' and be_age<2000 and contains(symbols,16#11C#) else '0';
    triggers(2)<='1' when msa_seen='1' and contains(symbols,16#1FB#) else '0';
    process(symbol_clk)
    begin
        if rising_edge(symbol_clk) then
            if triggers(0)='1' then msa_seen<='1';end if;
            if contains(symbols,16#1FB#) then be_age<=0;
            elsif be_age/=4095 then be_age<=be_age+1;end if;
        end if;
    end process;
    banks:for bank in 0 to 2 generate
        type ram_type is array(0 to depth-1) of std_logic_vector(39 downto 0);
        signal memory:ram_type;
        attribute ram_style:string;
        attribute ram_style of memory:signal is "block";
    begin
        process(symbol_clk)
        begin
            if rising_edge(symbol_clk) then
                if done(bank)='0' and (active(bank)='1' or triggers(bank)='1') then
                    memory(positions(bank))<=symbols;
                    if positions(bank)=depth-1 then
                        done(bank)<='1';active(bank)<='0';
                    else
                        positions(bank)<=positions(bank)+1;active(bank)<='1';
                    end if;
                end if;
            end if;
        end process;
        process(mgmt_clk)
        begin
            if rising_edge(mgmt_clk) then
                if done_mgmt(bank)='1' then bank_data(bank)<=memory(to_integer(read_address));
                else bank_data(bank)<=(others=>'0');end if;
            end if;
        end process;
    end generate;
    flags:xpm_cdc_array_single generic map(DEST_SYNC_FF=>3,INIT_SYNC_FF=>1,
        SIM_ASSERT_CHK=>1,SRC_INPUT_REG=>0,WIDTH=>3)
        port map(src_clk=>symbol_clk,src_in=>done,dest_clk=>mgmt_clk,dest_out=>done_mgmt);
    process(mgmt_clk)
    begin
        if rising_edge(mgmt_clk) then
            readable1<=done_mgmt;readable2<=readable1;
        end if;
    end process;
    complete<=readable2;
    with read_bank select read_data<=bank_data(0) when "00",bank_data(1) when "01",
                                    bank_data(2) when "10",(others=>'0') when others;
end;
