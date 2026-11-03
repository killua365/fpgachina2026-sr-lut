-- Passive TX buffer observation. UG482 v1.9 table 3-13: TXBUFSTATUS is
-- in TXUSRCLK2; bit 1 is sticky overflow/underflow, bit 0 is half-full.
-- Never changes reset, clock, drive or data. Counters saturate, not wrap.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library xpm;
use xpm.vcomponents.all;
entity gtp_health_monitor is
    generic(snapshot_interval:positive:=1350000);
    port(symbol_clk,mgmt_clk:in std_logic;
         buffer_status:in std_logic_vector(3 downto 0);
         running:in std_logic_vector(1 downto 0);
         link_ready:in std_logic;
         diagnostic:out std_logic_vector(63 downto 0));
end;
architecture rtl of gtp_health_monitor is
    signal state_meta,state_sync:std_logic_vector(2 downto 0):=(others=>'0');
    attribute ASYNC_REG:string;
    attribute ASYNC_REG of state_meta,state_sync:signal is "TRUE";
    attribute SHREG_EXTRACT:string;
    attribute SHREG_EXTRACT of state_meta,state_sync:signal is "NO";
    signal any_error,ready_error,stream_error,previous_error:std_logic_vector(1 downto 0):="00";
    signal previous_running:std_logic_vector(1 downto 0):="00";
    signal previous_active:std_logic:='0';
    signal sample_sequence:unsigned(15 downto 0):=(others=>'0');
    signal epochs,error0,error1,ready_losses:unsigned(7 downto 0):=(others=>'0');
    signal saturated:std_logic:='0';
    signal snapshot,received:std_logic_vector(63 downto 0):=(others=>'0');
    signal send,ack:std_logic:='0';
    signal count:natural range 0 to snapshot_interval-1:=0;
    signal startup:natural range 0 to 31:=0;
begin
    process(symbol_clk)
        variable errors:std_logic_vector(1 downto 0);
        variable active:std_logic;
        procedure increment(signal value:inout unsigned(7 downto 0)) is
        begin
            if value>=254 then saturated<='1'; end if;
            if value/=255 then value<=value+1; end if;
        end procedure;
    begin
        if rising_edge(symbol_clk) then
            state_meta<=link_ready & running;state_sync<=state_meta;
            if startup<31 then startup<=startup+1;end if;
            errors:=buffer_status(3) & buffer_status(1);
            active:='0';
            if state_sync="111" then active:='1';end if;
            any_error<=any_error or errors;
            if state_sync(1 downto 0)="11" then ready_error<=ready_error or errors;end if;
            if active='1' then stream_error<=stream_error or errors;end if;
            if errors(0)='1' and previous_error(0)='0' then increment(error0);end if;
            if errors(1)='1' and previous_error(1)='0' then increment(error1);end if;
            if active='1' and previous_active='0' then increment(epochs);end if;
            if (previous_running and not state_sync(1 downto 0))/="00" then increment(ready_losses);end if;
            previous_error<=errors;previous_running<=state_sync(1 downto 0);previous_active<=active;
            if count/=snapshot_interval-1 then count<=count+1;
            elsif send='0' and ack='0' and startup=31 then
                count<=0;sample_sequence<=sample_sequence+1;
                snapshot(3 downto 0)<=buffer_status;
                snapshot(5 downto 4)<=any_error;
                snapshot(7 downto 6)<=ready_error;
                snapshot(9 downto 8)<=stream_error;
                snapshot(11 downto 10)<=state_sync(1 downto 0);
                snapshot(12)<=state_sync(2);
                snapshot(13)<='1';snapshot(14)<=saturated;snapshot(15)<='1';
                snapshot(31 downto 16)<=std_logic_vector(sample_sequence);
                snapshot(39 downto 32)<=std_logic_vector(epochs);
                snapshot(47 downto 40)<=std_logic_vector(error0);
                snapshot(55 downto 48)<=std_logic_vector(error1);
                snapshot(63 downto 56)<=std_logic_vector(ready_losses);
                send<='1';
            end if;
            if ack='1' then send<='0';end if;
        end if;
    end process;
    snapshot_sync:xpm_cdc_handshake generic map(DEST_EXT_HSK=>0,DEST_SYNC_FF=>4,
        SRC_SYNC_FF=>4,INIT_SYNC_FF=>1,SIM_ASSERT_CHK=>1,WIDTH=>64)
        port map(src_clk=>symbol_clk,src_in=>snapshot,src_send=>send,src_rcv=>ack,
                 dest_clk=>mgmt_clk,dest_out=>received,dest_req=>open,dest_ack=>'0');
    diagnostic<=received;
end;
