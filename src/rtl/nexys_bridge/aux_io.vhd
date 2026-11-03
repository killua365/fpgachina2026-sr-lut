-- Nexys Video AUX pads: two differential pairs share one physical AUX bus.
-- Use a dedicated receiver and a tri-state transmitter as described in the
-- Digilent reference manual, Figure 14. Both banks/pairs use LVDS_25.
library ieee;
use ieee.std_logic_1164.all;
library unisim;
use unisim.vcomponents.all;

entity aux_io is
    port (
        tx_data : in std_logic;
        tx_disable : in std_logic;
        rx_data : out std_logic;
        rx_p, rx_n : in std_logic;
        tx_p, tx_n : out std_logic
    );
end entity;

architecture pads of aux_io is
begin
    receiver : IBUFDS
        generic map (DIFF_TERM => TRUE, IBUF_LOW_PWR => TRUE,
                     IOSTANDARD => "LVDS_25")
        port map (I => rx_p, IB => rx_n, O => rx_data);
    transmitter : OBUFTDS
        generic map (IOSTANDARD => "LVDS_25", SLEW => "SLOW")
        port map (I => tx_data, T => tx_disable, O => tx_p, OB => tx_n);
end architecture;
