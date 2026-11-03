-- Keep nine 50 ms slots: 450 ms fast counters, below 16-bit wrap bound.
-- Alternate GTP health and one legacy slow page in slot 8; EDID slows to 16.2 s/cycle.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity gtp_diagnostic_mux is
    generic(interval_cycles : positive := 5000000; enable_link_probe : boolean := false;
            enable_sink_power : boolean := false);
    port(clk : in std_logic;
         aux_status, counter0, counter1, counter2, sink_status : in std_logic_vector(63 downto 0);
         health : in std_logic_vector(63 downto 0);
         sink_power : in std_logic_vector(63 downto 0) := (others=>'0');
         edid_block : in std_logic_vector(1023 downto 0);
         edid_metadata : in std_logic_vector(63 downto 0);
         probe_errors, probe_time, probe_config, probe_metadata : in std_logic_vector(63 downto 0) := (others=>'0');
         kind : out std_logic_vector(7 downto 0);
         payload : out std_logic_vector(63 downto 0));
end;
architecture rtl of gtp_diagnostic_mux is
    signal count : natural range 0 to interval_cycles-1 := 0;
    signal fast_page : natural range 0 to 8 := 0;
    signal health_slot : std_logic := '1';
    signal latched_health : std_logic_vector(63 downto 0) := (others=>'0');
    signal latched_power : std_logic_vector(63 downto 0) := (others=>'0');
    signal power_slot : std_logic := '0';
    signal slow_page : natural range 0 to 17 := 0;
    signal latched_errors, latched_time, latched_config, latched_metadata : std_logic_vector(63 downto 0) := (others=>'0');
    function last_fast return natural is
    begin if enable_link_probe then return 8; else return 4; end if; end;
begin
    assert enable_link_probe report "GTP mux requires nine-slot probe cadence" severity failure;
    process(clk)
    begin
        if rising_edge(clk) then
            if count=interval_cycles-1 then
                count<=0;
                if fast_page=3 then
                    -- Four consecutive pages use one coherent snapshot, even
                    -- if the next read-clear poll completes during UART output.
                    latched_health<=health; latched_errors<=probe_errors; latched_time<=probe_time;
                    latched_power<=sink_power;
                    latched_config<=probe_config; latched_metadata<=probe_metadata;
                end if;
                if fast_page=last_fast then
                    fast_page<=0;
                    health_slot<=not health_slot;
                    if health_slot='1' and enable_sink_power then power_slot<=not power_slot; end if;
                    if health_slot='0' then
                        if slow_page=17 then slow_page<=0; else slow_page<=slow_page+1; end if;
                    end if;
                else fast_page<=fast_page+1; end if;
            else count<=count+1; end if;
        end if;
    end process;
    process(fast_page,slow_page,health_slot,power_slot,latched_power,latched_health,aux_status,counter0,counter1,counter2,sink_status,edid_block,edid_metadata,
            latched_errors,latched_time,latched_config,latched_metadata)
    begin
        kind<=std_logic_vector(to_unsigned(fast_page+1,8));
        case fast_page is
            when 0 => payload<=aux_status;
            when 1 => payload<=counter0;
            when 2 => payload<=counter1;
            when 3 => payload<=counter2;
            when others =>
              if enable_link_probe and fast_page<8 then
                kind<=std_logic_vector(to_unsigned(fast_page+19,8));
                case fast_page is
                    when 4=>payload<=latched_errors;
                    when 5=>payload<=latched_time;
                    when 6=>payload<=latched_config;
                    when others=>payload<=latched_metadata;
                end case;
              else
                if health_slot='1' then
                    -- 34..37 belong to immutable stream-capture pages.
                    if enable_sink_power and power_slot='1' then kind<=x"26";payload<=latched_power;
                    else kind<=x"21";payload<=latched_health;end if;
                else
                kind<=std_logic_vector(to_unsigned(slow_page+5,8));
                case slow_page is
                    when 0 => payload<=sink_status;
                    when 1 => payload<=edid_metadata;
                    when others => payload<=edid_block((slow_page-1)*64-1 downto (slow_page-2)*64);
                end case;
                end if;
              end if;
        end case;
    end process;
end;
