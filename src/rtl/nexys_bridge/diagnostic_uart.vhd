-- Read-only 64-bit atomic telemetry. 115200 8N1, 20 packets/s at 100 MHz.
-- Packet: ASCII SRDP, record kind (1 = original AUX status), sequence u16 LE,
-- payload u64 LE, XOR checksum. Kind and payload are snapshotted together.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity diagnostic_uart is
    generic (clock_hz : positive := 100000000; baud : positive := 115200;
             interval_cycles : positive := 5000000);
    port (clk : in std_logic; status : in std_logic_vector(63 downto 0);
          record_kind : in std_logic_vector(7 downto 0) := x"01";
          tx : out std_logic := '1');
end entity;
architecture rtl of diagnostic_uart is
    constant divisor : positive := (clock_hz + baud/2)/baud;
    type packet_type is array(0 to 15) of std_logic_vector(7 downto 0);
    signal packet : packet_type := (others => (others=>'0'));
    signal interval_count : natural range 0 to interval_cycles-1 := 0;
    signal baud_count : natural range 0 to divisor-1 := 0;
    signal byte_index : natural range 0 to 15 := 0;
    signal bit_index : natural range 0 to 9 := 0;
    signal shift : std_logic_vector(9 downto 0) := (others=>'1');
    signal busy : std_logic := '0';
    signal sequence_id : unsigned(15 downto 0) := (others=>'0');
begin
    tx <= shift(0) when busy='1' else '1';
    process(clk)
        variable next_packet : packet_type;
        variable checksum : std_logic_vector(7 downto 0);
    begin
        if rising_edge(clk) then
            if interval_count /= interval_cycles-1 then
                interval_count <= interval_count+1;
            elsif busy='0' then
                interval_count <= 0;
                next_packet(0):=x"53"; next_packet(1):=x"52";
                next_packet(2):=x"44"; next_packet(3):=x"50"; next_packet(4):=record_kind;
                next_packet(5):=std_logic_vector(sequence_id(7 downto 0));
                next_packet(6):=std_logic_vector(sequence_id(15 downto 8));
                for i in 0 to 7 loop next_packet(7+i):=status(8*i+7 downto 8*i); end loop;
                checksum := (others=>'0');
                for i in 0 to 14 loop checksum := checksum xor next_packet(i); end loop;
                next_packet(15):=checksum;
                packet <= next_packet;
                sequence_id <= sequence_id+1;
                byte_index<=0; bit_index<=0; baud_count<=divisor-1;
                shift <= '1' & next_packet(0) & '0'; busy <= '1';
            end if;
            if busy='1' then
                if baud_count /= 0 then baud_count<=baud_count-1;
                else
                    baud_count<=divisor-1;
                    if bit_index=9 then
                        bit_index<=0;
                        if byte_index=15 then busy<='0'; shift<=(others=>'1');
                        else
                            byte_index<=byte_index+1;
                            shift<='1' & packet(byte_index+1) & '0';
                        end if;
                    else
                        bit_index<=bit_index+1;
                        shift<='1' & shift(9 downto 1);
                    end if;
                end if;
            end if;
        end if;
    end process;
end architecture;
