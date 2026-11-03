-- Passive source / post-scrambler marker counters. Never affects the video path.
-- Snapshot all counters in the source domain, then transfer the stable record
-- using the AMD XPM full handshake. Fast counter changes never cross directly.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library xpm;
use xpm.vcomponents.all;
entity stream_diagnostic is
    generic(snapshot_interval : positive := 1350000);
    port(symbol_clk,mgmt_clk : in std_logic;
         source_ready : in std_logic;
         source_data : in std_logic_vector(72 downto 0);
         tx_symbols : in std_logic_vector(79 downto 0);
         counters : out std_logic_vector(159 downto 0);
         ready_sync : out std_logic);
end;
architecture rtl of stream_diagnostic is
    subtype tx_pair is std_logic_vector(19 downto 0);
    type counter_array is array(0 to 7) of unsigned(15 downto 0);
    signal events : counter_array := (others=>(others=>'0'));
    signal ticks : unsigned(31 downto 0) := (others=>'0');
    signal snapshot : std_logic_vector(160 downto 0) := (others=>'0');
    signal received : std_logic_vector(160 downto 0);
    signal send,ack : std_logic := '0';
    signal snapshot_count : natural range 0 to snapshot_interval-1 := 0;
    function has_source(value : std_logic_vector; code : std_logic_vector(7 downto 0)) return boolean is
    begin
        return value(8 downto 0)=('1'&code) or value(17 downto 9)=('1'&code);
    end;
    function has_tx(value : tx_pair; code : std_logic_vector(7 downto 0)) return boolean is
    begin
        return value(8 downto 0)=('1'&code) or value(18 downto 10)=('1'&code);
    end;
begin
    process(symbol_clk)
    begin
        if rising_edge(symbol_clk) then
            ticks<=ticks+1;
            if has_source(source_data,x"BC") then events(0)<=events(0)+1; end if;
            if has_source(source_data,x"5C") then events(1)<=events(1)+1; end if;
            if has_tx(tx_symbols(19 downto 0),x"FB") then events(2)<=events(2)+1; end if;
            if has_tx(tx_symbols(39 downto 20),x"FB") then events(3)<=events(3)+1; end if;
            if has_tx(tx_symbols(19 downto 0),x"BC") or has_tx(tx_symbols(19 downto 0),x"1C") then events(4)<=events(4)+1; end if;
            if has_tx(tx_symbols(19 downto 0),x"5C") then events(5)<=events(5)+1; end if;
            if has_tx(tx_symbols(39 downto 20),x"5C") then events(6)<=events(6)+1; end if;
            if has_tx(tx_symbols(39 downto 20),x"BC") or has_tx(tx_symbols(39 downto 20),x"1C") then events(7)<=events(7)+1; end if;
            if snapshot_count/=snapshot_interval-1 then snapshot_count<=snapshot_count+1;
            elsif send='0' and ack='0' then
                snapshot_count<=0;
                snapshot(31 downto 0)<=std_logic_vector(ticks);
                for i in 0 to 7 loop snapshot(47+16*i downto 32+16*i)<=std_logic_vector(events(i)); end loop;
                snapshot(160)<=source_ready;
                send<='1';
            end if;
            if ack='1' then send<='0'; end if;
        end if;
    end process;
    snapshot_sync: xpm_cdc_handshake generic map(DEST_EXT_HSK=>0,DEST_SYNC_FF=>4,
        SRC_SYNC_FF=>4,INIT_SYNC_FF=>1,SIM_ASSERT_CHK=>1,WIDTH=>161)
        port map(src_clk=>symbol_clk,src_in=>snapshot,src_send=>send,src_rcv=>ack,
                 dest_clk=>mgmt_clk,dest_out=>received,dest_req=>open,dest_ack=>'0');
    counters<=received(159 downto 0);
    ready_sync<=received(160);
end;
