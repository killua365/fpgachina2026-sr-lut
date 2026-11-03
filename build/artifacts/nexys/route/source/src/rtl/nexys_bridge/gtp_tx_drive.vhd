-- SPDX-License-Identifier: MIT
-- Nexys Video GTPE2 HBR drive profile. UG482 tables 3-28/3-29.
-- These are nominal engineering settings, NOT measured connector voltages.
-- Keep TXBUFDIFFCTRL=100, DIRECT mode and automatic main cursor in the GTP.
-- See report/current/nexys_tx_drive_validation.md for derivation and limits.
library ieee;
use ieee.std_logic_1164.all;

entity gtp_tx_drive is
    port (
        clk : in std_logic;
        voltage_onehot, emphasis_onehot : in std_logic_vector(2 downto 0);
        diffctrl : out std_logic_vector(3 downto 0) := "0010";
        precursor : out std_logic_vector(4 downto 0) := "00000";
        inhibit : out std_logic := '1'
    );
end;

architecture rtl of gtp_tx_drive is
begin
    process(clk)
        variable v, p : integer range -1 to 2;
    begin
        if rising_edge(clk) then
            v := -1; p := -1;
            case voltage_onehot is
                when "001" => v := 0;
                when "010" => v := 1;
                when "100" => v := 2;
                when others => null;
            end case;
            case emphasis_onehot is
                when "001" => p := 0;
                when "010" => p := 1;
                when "100" => p := 2;
                when others => null;
            end case;
            -- Reject unknown/multiple levels and unsupported V2/P2. Do not
            -- silently transmit settings different from the AUX declaration.
            diffctrl <= "0010"; precursor <= "00000"; inhibit <= '1';
            if v >= 0 and p >= 0 and v+p <= 3 then
                inhibit <= '0';
                case p is
                    when 0 =>
                        precursor <= "00000";
                        case v is
                            when 0 => diffctrl <= "0010";
                            when 1 => diffctrl <= "0110";
                            when others => diffctrl <= "1001";
                        end case;
                    when 1 =>
                        precursor <= "01101";
                        case v is
                            when 0 => diffctrl <= "0110";
                            when 1 => diffctrl <= "1011";
                            when others => diffctrl <= "1111";
                        end case;
                    when others =>
                        precursor <= "10100";
                        if v=0 then diffctrl <= "1001";
                        else diffctrl <= "1111"; end if;
                end case;
            end if;
        end if;
    end process;
end;
