library ieee;
use ieee.std_logic_1164.all;
package sink_scenario is
    signal selected_case : natural := 0;
end package;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.sink_scenario.all;

-- Replace only the physical AUX serializer with a packet-level sink model.
-- The candidate AUX FSM and actual DP request ROM are compiled unchanged.
entity aux_interface is
    port(clk: in std_logic; debug_pmod: out std_logic_vector(7 downto 0);
         dp_tx_aux_p,dp_tx_aux_n: out std_logic;
         dp_rx_aux_p,dp_rx_aux_n: in std_logic;
         tx_wr_en: in std_logic; tx_data: in std_logic_vector(7 downto 0); tx_full: out std_logic;
         rx_rd_en: in std_logic; rx_data: out std_logic_vector(7 downto 0); rx_empty: out std_logic;
         busy,timeout: out std_logic);
end entity;

architecture mock of aux_interface is
    type bytes is array(0 to 31) of std_logic_vector(7 downto 0);
    signal response: bytes := (others=>x"00");
    signal rd,nresponse: natural range 0 to 32 := 0;
    signal waiting: natural range 0 to 10 := 0;
    signal collecting: std_logic := '0';
begin
    debug_pmod <= (others=>'0');
    dp_tx_aux_p <= '0'; dp_tx_aux_n <= '0'; tx_full <= '0'; timeout <= '0';
    rx_empty <= '1' when rd>=nresponse else '0';
    busy <= '1' when tx_wr_en='1' or collecting='1' or waiting>0 else '0';
    process(clk)
        variable request: bytes := (others=>x"00");
        variable count: natural range 0 to 32 := 0;
        variable answer: bytes;
        variable length: natural range 0 to 32;
        variable power_was_written: boolean := false;
        variable pre_value,post_value: natural;
    begin
        if rising_edge(clk) then
            if rx_rd_en='1' and rd<nresponse then
                rx_data <= response(rd); rd<=rd+1;
            end if;
            if tx_wr_en='1' then
                assert count<32 severity failure;
                request(count):=tx_data; count:=count+1; collecting<='1';
            elsif collecting='1' then
                collecting<='0'; waiting<=8;
                answer:=(others=>x"00"); length:=1;
                if (request(0)=x"50" or request(0)=x"90") and count=4 then
                    length:=to_integer(unsigned(request(3)))+2;
                end if;
                if request(0)=x"90" and request(1)=x"06" and request(2)=x"00" then
                    case selected_case is
                        when 0 => pre_value:=1;post_value:=1;
                        when 1 => pre_value:=2;post_value:=1;
                        when 2 => pre_value:=0;post_value:=1;
                        when 3 => pre_value:=0;post_value:=0;
                        when 4 => pre_value:=0;post_value:=2;
                        when 5 => pre_value:=2;post_value:=0;
                        when 8 => pre_value:=3;post_value:=1;
                        when others => pre_value:=0;post_value:=1;
                    end case;
                    if power_was_written then
                        answer(1):=std_logic_vector(to_unsigned(post_value,8));
                    else
                        answer(1):=std_logic_vector(to_unsigned(pre_value,8));
                    end if;
                    if selected_case=7 then length:=1;end if; -- truncated pre-read
                elsif request(0)=x"80" and request(1)=x"06" and request(2)=x"00" then
                    assert request(4)=x"01" report "Unexpected sink power write" severity failure;
                    if selected_case=6 then answer(0):=x"10";
                    else power_was_written:=true;end if;
                end if;
                response<=answer; rd<=0; nresponse<=0;
                -- nresponse is released only after packet transmission ends.
                request(31):=std_logic_vector(to_unsigned(length,8));
                count:=0;
            elsif waiting>0 then
                waiting<=waiting-1;
                if waiting=1 then nresponse<=to_integer(unsigned(request(31)));end if;
            end if;
        end if;
    end process;
end architecture;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.sink_scenario.all;
use std.env.all;

entity tb_vga_power is
    generic(CASE_ID: natural := 0);
end entity;

architecture test of tb_vga_power is
    signal clk: std_logic := '0';
    signal present: std_logic := '1';
    signal diag: std_logic_vector(47 downto 0);
    signal power: std_logic_vector(63 downto 0);
    signal established,clock_train: std_logic;
    signal saw_clock: boolean := false;
begin
    selected_case<=CASE_ID;
    clk<=not clk after 5 ns;
    dut: entity work.aux_channel
        generic map(initialize_sink_power=>true)
        port map(clk=>clk,debug_pmod=>open,diagnostic_status=>diag,sink_power_status=>power,
            edid_de=>open,dp_reg_de=>open,adjust_de=>open,status_de=>open,aux_addr=>open,aux_data=>open,
            enhanced_framing=>'1',link_count=>"010",hpd_irq=>'0',hpd_present=>present,
            sink_capable=>'1',tx_ready=>'1',dpcd_revision=>x"11",capabilities_reset=>open,
            tx_powerup=>open,tx_clock_train=>clock_train,tx_align_train=>open,tx_link_established=>established,
            swing_0p4=>'1',swing_0p6=>'0',swing_0p8=>'0',preemp_0p0=>'1',preemp_3p5=>'0',preemp_6p0=>'0',
            clock_locked=>'0',equ_locked=>'0',symbol_locked=>'0',align_locked=>'0',
            dp_tx_hp_detect=>'1',dp_tx_aux_p=>open,dp_tx_aux_n=>open,dp_rx_aux_p=>'1',dp_rx_aux_n=>'0');
    process(clk)
    begin
        if rising_edge(clk) and clock_train='1' then saw_clock<=true;end if;
    end process;
    process
    begin
        wait for 5 ms;
        assert established='0' report "Power quirk falsely claimed a trained link" severity failure;
        if CASE_ID<=3 then
            assert saw_clock report "Valid power sequence failed to reach real training" severity failure;
            if CASE_ID=3 then
                assert power(36)='1' and power(35)='0'
                    report "Zero-register quirk not distinguished from verified D0" severity failure;
            else
                assert power(36)='0' and power(35)='1'
                    report "Normal D0 verification changed" severity failure;
            end if;
        else
            assert not saw_clock report "Rejected power response reached training" severity failure;
            assert power(36)='0' report "Invalid response enabled zero quirk" severity failure;
        end if;
        present<='0'; wait for 100 ns;
        assert established='0' and clock_train='0' report "HPD loss did not stop training" severity failure;
        report "VGA_POWER_CASE_PASS " & integer'image(CASE_ID) severity note;
        finish;
        wait;
    end process;
end architecture;
