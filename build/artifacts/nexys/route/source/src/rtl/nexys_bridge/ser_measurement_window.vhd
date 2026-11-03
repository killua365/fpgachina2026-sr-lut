-- SER read-clear scheduling, isolated from the normal-video polling path.
-- Not connected to a hardware top yet. The caller must qualify the actual
-- SER stream, DPCD 1.1 mode/lane ACKs and configuration before qualified=1.
-- All counter reads must pass through this owner; otherwise external_clear
-- must pulse. A reply is successful only after all four payload bytes arrive.
-- Clock is management clock. Tags survive qualification loss; the adapter
-- must echo the accepted tag and honor cancel. FPGA configuration flushes it.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity ser_measurement_window is
    generic(cycles_per_ms : positive := 100000;
            window_ms : positive range 10000 to 99000 := 20000;
            timeout_ms : positive := 1000);
    port(clk : in std_logic;
         qualified, external_clear : in std_logic;
         request_valid : out std_logic;
         request_ready : in std_logic;
         request_tag : out unsigned(31 downto 0);
         cancel : out std_logic := '0';
         reply_valid, reply_success : in std_logic;
         reply_tag : in unsigned(31 downto 0);
         reply_data : in std_logic_vector(31 downto 0);
         sample_valid, sample_success, interval_known : out std_logic := '0';
         sample_raw : out std_logic_vector(31 downto 0) := (others=>'0');
         lane_valid, lane_saturated : out std_logic_vector(1 downto 0) := "00";
         interval_cycles : out unsigned(63 downto 0) := (others=>'0');
         interval_min_cycles, interval_max_cycles : out unsigned(63 downto 0) := (others=>'0');
         -- 0: timed success, 1: baseline, 2: timeout, 3: AUX failure,
         -- 4: qualification lost, 5: another read/clear, 6: window outside range.
         reason : out unsigned(2 downto 0) := (others=>'0');
         epoch : out unsigned(31 downto 0) := (others=>'0'));
end entity;

architecture rtl of ser_measurement_window is
    type states is (dormant, send_read, await_reply, wait_window, backoff);
    signal state : states := dormant;
    signal clock_count, baseline_time, phase_time : unsigned(63 downto 0) := (others=>'0');
    signal accepted_time, baseline_start : unsigned(63 downto 0) := (others=>'0');
    signal tag, epoch_count : unsigned(31 downto 0) := (others=>'0');
    signal baseline_present : boolean := false;
    constant target_cycles : unsigned(63 downto 0) := to_unsigned(cycles_per_ms,32)*to_unsigned(window_ms,32);
    constant minimum_cycles : unsigned(63 downto 0) := to_unsigned(cycles_per_ms,32)*to_unsigned(10000,32);
    constant maximum_cycles : unsigned(63 downto 0) := to_unsigned(cycles_per_ms,32)*to_unsigned(100000,32);
    constant timeout_cycles : unsigned(63 downto 0) := to_unsigned(cycles_per_ms,32)*to_unsigned(timeout_ms,32);
begin
    request_valid <= '1' when state=send_read and qualified='1' and external_clear='0'
                             and clock_count-phase_time<timeout_cycles else '0';
    request_tag<=tag; epoch<=epoch_count;
    process(clk)
        variable elapsed, lower_bound, upper_bound : unsigned(63 downto 0);
        variable window_ok : boolean;
    begin
        if rising_edge(clk) then
            clock_count<=clock_count+1;
            sample_valid<='0'; cancel<='0';
            if qualified='0' or external_clear='1' then
                if state=send_read or state=await_reply then
                    cancel<='1'; sample_valid<='1'; sample_success<='0';
                    interval_known<='0'; sample_raw<=(others=>'0');
                    lane_valid<="00"; lane_saturated<="00"; interval_cycles<=(others=>'0');
                    interval_min_cycles<=(others=>'0'); interval_max_cycles<=(others=>'0');
                    if qualified='0' then reason<=to_unsigned(4,3);
                    else reason<=to_unsigned(5,3); end if;
                end if;
                baseline_present<=false;
                if qualified='0' then state<=dormant;
                else state<=backoff; phase_time<=clock_count; end if;
            else
                case state is
                    when dormant =>
                        epoch_count<=epoch_count+1; tag<=tag+1;
                        phase_time<=clock_count; state<=send_read;
                    when send_read =>
                        -- Timeout wins at the boundary; never accept a request
                        -- after the owner's deadline and then forget its reply.
                        if clock_count-phase_time>=timeout_cycles then
                            cancel<='1'; sample_valid<='1'; sample_success<='0';
                            interval_known<='0'; sample_raw<=(others=>'0');
                            lane_valid<="00"; lane_saturated<="00"; interval_cycles<=(others=>'0');
                            interval_min_cycles<=(others=>'0'); interval_max_cycles<=(others=>'0');
                            reason<=to_unsigned(2,3); baseline_present<=false;
                            state<=backoff; phase_time<=clock_count;
                        elsif request_ready='1' then
                            phase_time<=clock_count; accepted_time<=clock_count; state<=await_reply;
                        end if;
                    when await_reply =>
                        if clock_count-phase_time>=timeout_cycles then
                            cancel<='1'; sample_valid<='1'; sample_success<='0';
                            interval_known<='0'; sample_raw<=(others=>'0');
                            lane_valid<="00"; lane_saturated<="00"; interval_cycles<=(others=>'0');
                            interval_min_cycles<=(others=>'0'); interval_max_cycles<=(others=>'0');
                            reason<=to_unsigned(2,3); baseline_present<=false;
                            state<=backoff; phase_time<=clock_count;
                        elsif reply_valid='1' and reply_tag=tag then
                            sample_valid<='1'; sample_success<=reply_success;
                            interval_known<='0'; sample_raw<=(others=>'0');
                            lane_valid<="00"; lane_saturated<="00"; interval_cycles<=(others=>'0');
                            interval_min_cycles<=(others=>'0'); interval_max_cycles<=(others=>'0');
                            if reply_success='1' then
                                sample_raw<=reply_data;
                                elapsed:=clock_count-baseline_time;
                                -- The sink clears between request acceptance
                                -- and reply completion, not at our ACK timestamp.
                                -- Bound both operations instead of overstating
                                -- a completion-to-completion duration as exact.
                                lower_bound:=accepted_time-baseline_time;
                                upper_bound:=clock_count-baseline_start;
                                window_ok:=baseline_present and lower_bound>=minimum_cycles and upper_bound<=maximum_cycles;
                                if window_ok then
                                    interval_known<='1'; interval_cycles<=elapsed; reason<=to_unsigned(0,3);
                                    interval_min_cycles<=lower_bound; interval_max_cycles<=upper_bound;
                                    for lane in 0 to 1 loop
                                        lane_valid(lane)<=reply_data(16*lane+15);
                                        if reply_data(16*lane+15 downto 16*lane)=x"FFFF" then
                                            lane_saturated(lane)<='1';
                                        end if;
                                    end loop;
                                elsif baseline_present then reason<=to_unsigned(6,3);
                                else reason<=to_unsigned(1,3); end if;
                                -- Even an invalid sink valid bit does not undo
                                -- the completed read-clear operation itself.
                                baseline_present<=true; baseline_time<=clock_count;
                                baseline_start<=accepted_time;
                                state<=wait_window;
                            else
                                baseline_present<=false; reason<=to_unsigned(3,3);
                                state<=backoff; phase_time<=clock_count;
                            end if;
                        end if;
                    when wait_window =>
                        if clock_count-baseline_time>=target_cycles then
                            tag<=tag+1; phase_time<=clock_count; state<=send_read;
                        end if;
                    when backoff =>
                        if clock_count-phase_time>=timeout_cycles then
                            tag<=tag+1; phase_time<=clock_count; state<=send_read;
                        end if;
                end case;
            end if;
        end if;
    end process;
end architecture;
