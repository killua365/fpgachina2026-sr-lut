-- SR-LUT EDID base-block decoder. Interface derived from FPGA_DisplayPort.
-- Copyright (c) 2015 Michael Alan Field <hamster@snap.net.nz>
-- SPDX-License-Identifier: MIT
-- The upstream MIT license is retained at src/third_party/fpga_displayport/LICENSE.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity edid_decode is
    port (
        clk, edid_de : in std_logic;
        edid_data, edid_addr : in std_logic_vector(7 downto 0);
        invalidate : in std_logic;
        valid : out std_logic := '0';
        support_RGB444, support_YCC444, support_YCC422 : out std_logic := '0';
        pixel_clock_x10k : out std_logic_vector(15 downto 0) := (others=>'0');
        h_visible_len, h_blank_len, h_front_len, h_sync_len : out std_logic_vector(11 downto 0) := (others=>'0');
        v_visible_len, v_blank_len, v_front_len, v_sync_len : out std_logic_vector(11 downto 0) := (others=>'0');
        interlaced : out std_logic := '0'
    );
end entity;

architecture validated of edid_decode is
    type block_type is array (0 to 126) of std_logic_vector(7 downto 0);
    signal bytes : block_type;
    signal receiving : std_logic := '0';
    signal next_addr : natural range 1 to 127 := 1;
    signal checksum : unsigned(7 downto 0) := (others=>'0');
begin
    process(clk)
        variable address_value : natural;
    begin
        if rising_edge(clk) then
            if invalidate='1' then
                valid <= '0'; receiving <= '0'; next_addr <= 1;
            elsif edid_de='1' then
                address_value := to_integer(unsigned(edid_addr));
                if address_value=0 then
                    bytes(0) <= edid_data;
                    checksum <= unsigned(edid_data);
                    receiving <= '1'; next_addr <= 1; valid <= '0';
                elsif receiving='1' and address_value=next_addr then
                    checksum <= checksum+unsigned(edid_data);
                    if address_value=127 then
                        receiving <= '0'; valid <= '0';
                        if checksum+unsigned(edid_data)=0 and
                           bytes(0)=x"00" and bytes(1)=x"FF" and bytes(2)=x"FF" and
                           bytes(3)=x"FF" and bytes(4)=x"FF" and bytes(5)=x"FF" and
                           bytes(6)=x"FF" and bytes(7)=x"00" then
                            -- Publish only after all 128 bytes, header and checksum
                            -- have passed. EDID validity is not mode acceptance.
                            valid <= '1';
                            support_RGB444 <= bytes(20)(7);
                            support_YCC444 <= '0'; support_YCC422 <= '0';
                            if bytes(20)(7)='1' and bytes(18)=x"01" and bytes(19)>=x"04" then
                                support_YCC444 <= bytes(24)(3);
                                support_YCC422 <= bytes(24)(4);
                            end if;
                            pixel_clock_x10k <= bytes(55) & bytes(54);
                            h_visible_len <= bytes(58)(7 downto 4) & bytes(56);
                            h_blank_len <= bytes(58)(3 downto 0) & bytes(57);
                            v_visible_len <= bytes(61)(7 downto 4) & bytes(59);
                            v_blank_len <= bytes(61)(3 downto 0) & bytes(60);
                            h_front_len <= "00" & bytes(65)(7 downto 6) & bytes(62);
                            h_sync_len <= "00" & bytes(65)(5 downto 4) & bytes(63);
                            v_front_len <= "000000" & bytes(65)(3 downto 2) & bytes(64)(7 downto 4);
                            v_sync_len <= "000000" & bytes(65)(1 downto 0) & bytes(64)(3 downto 0);
                            interlaced <= bytes(71)(7);
                        end if;
                    else
                        bytes(address_value) <= edid_data;
                        next_addr <= next_addr+1;
                    end if;
                else
                    -- Missing/duplicate/out-of-order data cannot complete a block.
                    receiving <= '0'; valid <= '0';
                end if;
            end if;
        end if;
    end process;
end architecture;
