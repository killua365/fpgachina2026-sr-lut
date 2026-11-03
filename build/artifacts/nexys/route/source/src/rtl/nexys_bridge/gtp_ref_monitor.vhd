-- One reference-domain toggle and ONE synchronization chain shared by lanes.
-- Both lane controllers receive the same management-domain observation.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity gtp_ref_monitor is
    port (ref_clk, mgmt_clk : in std_logic; observed_toggle : out std_logic);
end entity;
architecture rtl of gtp_ref_monitor is
    signal counter : unsigned(7 downto 0) := (others=>'0');
    signal toggle_meta, toggle_sync : std_logic := '0';
    attribute ASYNC_REG : string;
    attribute ASYNC_REG of toggle_meta, toggle_sync : signal is "TRUE";
    attribute SHREG_EXTRACT : string;
    attribute SHREG_EXTRACT of toggle_meta, toggle_sync : signal is "NO";
begin
    process(ref_clk) begin
        if rising_edge(ref_clk) then counter<=counter+1; end if;
    end process;
    process(mgmt_clk) begin
        if rising_edge(mgmt_clk) then
            toggle_meta<=counter(7); toggle_sync<=toggle_meta;
        end if;
    end process;
    observed_toggle<=toggle_sync;
end architecture;
