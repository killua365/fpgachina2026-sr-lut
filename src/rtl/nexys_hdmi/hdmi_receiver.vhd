library ieee;
use ieee.std_logic_1164.all;
library unisim;
use unisim.vcomponents.all;

-- The reference clock must be stable before ref_ready is asserted. DDC and HPD
-- are sink functions; no HDMI transmitter, audio or encrypted input is implied.
entity hdmi_receiver is
    generic (EDID_FILE : string := "n4_1080p30.data");
    port (ref200,ref_ready : in std_logic;
        tmds_clk_p,tmds_clk_n : in std_logic;
        tmds_data_p,tmds_data_n : in std_logic_vector(2 downto 0);
        ddc_scl,ddc_sda : inout std_logic;hpd : out std_logic;
        pixel_clk,locked,de,hs,vs : out std_logic;
        rbg : out std_logic_vector(23 downto 0));
end entity;
architecture rtl of hdmi_receiver is
    signal si,so,st,ci,co,ct : std_logic;
begin
    hpd <= ref_ready;
    sda_buffer: IOBUF port map(I=>so,T=>st,O=>si,IO=>ddc_sda);
    scl_buffer: IOBUF port map(I=>co,T=>ct,O=>ci,IO=>ddc_scl);
    receiver: entity work.dvi2rgb
        generic map(kEmulateDDC=>true,kRstActiveHigh=>true,kAddBUFG=>true,
            kClkRange=>2,kEdidFileName=>EDID_FILE,kDebug=>false)
        port map(TMDS_Clk_p=>tmds_clk_p,TMDS_Clk_n=>tmds_clk_n,
            TMDS_Data_p=>tmds_data_p,TMDS_Data_n=>tmds_data_n,
            RefClk=>ref200,aRst=>not ref_ready,aRst_n=>'1',
            vid_pData=>rbg,vid_pVDE=>de,vid_pHSync=>hs,vid_pVSync=>vs,
            PixelClk=>pixel_clk,SerialClk=>open,aPixelClkLckd=>open,pLocked=>locked,
            SDA_I=>si,SDA_O=>so,SDA_T=>st,SCL_I=>ci,SCL_O=>co,SCL_T=>ct,
            pRst=>'0',pRst_n=>'1');
end architecture;
