-- SPDX-License-Identifier: MIT
-- Replacement for the interface in Mike Field's FPGA_DisplayPort (MIT, 2015).
-- 100 MHz HPD classification; no AUX requests or transceiver control here.
-- AMD PG199: IRQ low pulse 500 us..1 ms; disconnect low >2 ms.
library ieee;
use ieee.std_logic_1164.all;

entity hotplug_decode is
    port (clk : in std_logic;
          hpd : in std_logic;
          irq : out std_logic := '0';
          present : out std_logic := '0');
end entity;

architecture rtl of hotplug_decode is
    constant CONNECT_CYCLES : positive := 30001; -- stable high >250 us
    constant IRQ_MIN_CYCLES : positive := 50000;
    constant IRQ_MAX_CYCLES : positive := 100000;
    constant DISCONNECT_CYCLES : positive := 200001;
    signal hpd_meta, hpd_sync : std_logic := '0';
    signal connected : std_logic := '0';
    signal high_cycles : natural range 0 to CONNECT_CYCLES := 0;
    signal low_cycles : natural range 0 to DISCONNECT_CYCLES := 0;
    attribute ASYNC_REG : string;
    attribute ASYNC_REG of hpd_meta, hpd_sync : signal is "TRUE";
begin
    present <= connected;
    process(clk)
    begin
        if rising_edge(clk) then
            hpd_meta <= hpd;
            hpd_sync <= hpd_meta;
            irq <= '0';
            if hpd_sync = '1' then
                if connected = '1' and low_cycles >= IRQ_MIN_CYCLES
                   and low_cycles <= IRQ_MAX_CYCLES then
                    irq <= '1';
                end if;
                low_cycles <= 0;
                if high_cycles < CONNECT_CYCLES then
                    high_cycles <= high_cycles + 1;
                end if;
                if high_cycles = CONNECT_CYCLES - 1 then
                    connected <= '1';
                end if;
            else
                high_cycles <= 0;
                if low_cycles < DISCONNECT_CYCLES then
                    low_cycles <= low_cycles + 1;
                end if;
                if low_cycles = DISCONNECT_CYCLES - 1 then
                    connected <= '0';
                end if;
            end if;
        end if;
    end process;
end architecture;
