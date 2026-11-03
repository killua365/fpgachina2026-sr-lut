-- SR-LUT atomic DPCD capability decoder; FPGA_DisplayPort-compatible interface.
-- Copyright (c) 2015 Michael Alan Field <hamster@snap.net.nz>
-- SPDX-License-Identifier: MIT
-- The upstream MIT license is retained at src/third_party/fpga_displayport/LICENSE.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dp_register_decode is
    port (
        clk, de : in std_logic;
        data, addr : in std_logic_vector(7 downto 0);
        invalidate : in std_logic;
        valid : out std_logic := '0';
        revision : out std_logic_vector(7 downto 0) := (others=>'0');
        link_rate_2_70, link_rate_1_62, extended_framing : out std_logic := '0';
        link_count : out std_logic_vector(3 downto 0) := (others=>'0');
        max_downspread, coding_supported : out std_logic_vector(7 downto 0) := (others=>'0');
        port0_capabilities, port1_capabilities : out std_logic_vector(15 downto 0) := (others=>'0');
        norp : out std_logic_vector(7 downto 0) := (others=>'0');
        training_interval : out std_logic_vector(6 downto 0) := (others=>'0')
    );
end entity;

architecture validated of dp_register_decode is
    type block_type is array(0 to 14) of std_logic_vector(7 downto 0);
    signal bytes : block_type;
    signal receiving : std_logic := '0';
    signal next_addr : natural range 1 to 15 := 1;
begin
    process(clk)
        variable address_value : natural;
    begin
        if rising_edge(clk) then
            if invalidate='1' then
                valid <= '0'; receiving <= '0'; next_addr <= 1;
            elsif de='1' then
                address_value := to_integer(unsigned(addr));
                if address_value=0 then
                    bytes(0) <= data; receiving <= '1'; next_addr <= 1; valid <= '0';
                elsif receiving='1' and address_value=next_addr then
                    if address_value=15 then
                        receiving <= '0'; valid <= '1';
                        revision <= bytes(0);
                        link_rate_1_62 <= '0'; link_rate_2_70 <= '0';
                        case bytes(1) is
                            when x"06" => link_rate_1_62 <= '1';
                            when x"0A" | x"14" | x"1E" =>
                                link_rate_1_62 <= '1'; link_rate_2_70 <= '1';
                            when others => null;
                        end case;
                        extended_framing <= bytes(2)(7);
                        -- Only standard lane counts are usable. Preserve valid
                        -- for a complete block, while invalid lane codes become 0.
                        link_count <= (others=>'0');
                        case bytes(2)(4 downto 0) is
                            when "00001" => link_count <= x"1";
                            when "00010" => link_count <= x"2";
                            when "00100" => link_count <= x"4";
                            when others => null;
                        end case;
                        max_downspread <= bytes(3); norp <= bytes(4);
                        coding_supported <= bytes(6);
                        port0_capabilities <= bytes(9) & bytes(8);
                        port1_capabilities <= bytes(11) & bytes(10);
                        training_interval <= bytes(14)(6 downto 0);
                    else
                        bytes(address_value) <= data;
                        next_addr <= next_addr+1;
                    end if;
                else
                    receiving <= '0'; valid <= '0';
                end if;
            end if;
        end if;
    end process;
end architecture;
