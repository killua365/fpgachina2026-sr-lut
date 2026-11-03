// N4 HDMI / complete-frame DDR / VDMA / DP system. SRAM candidate only.
module bridge_board_top_continuous #(parameter [31:0] CANDIDATE_ID=32'h26100901,
    parameter EDID_FILE="n4_1080p30.data",parameter integer BUSINESS_IDENTITY=0)(
    input wire clk100,output wire diagnostic_tx,output wire [7:0] led,
    input wire hdmi_clk_p,hdmi_clk_n,input wire [2:0] hdmi_data_p,hdmi_data_n,
    inout wire hdmi_scl,hdmi_sda,output wire hdmi_hpd,hdmi_rx_txen,
    inout wire [15:0] ddr3_dq,inout wire [1:0] ddr3_dqs_n,ddr3_dqs_p,
    output wire [14:0] ddr3_addr,output wire [2:0] ddr3_ba,
    output wire ddr3_ras_n,ddr3_cas_n,ddr3_we_n,ddr3_reset_n,
    output wire [0:0] ddr3_ck_p,ddr3_ck_n,ddr3_cke,ddr3_odt,
    output wire [1:0] ddr3_dm,
    output wire [1:0] gtptxp,gtptxn,
    input wire refclk0_p,refclk0_n,dp_tx_hp_detect,
    output wire dp_tx_aux_p,dp_tx_aux_n,input wire dp_rx_aux_p,dp_rx_aux_n
);
    wire input_clock,root_clk,ref200_unbuffered,ref200,feedback,feedback_global,ref_locked;
    IBUF board_clock_input(.I(clk100),.O(input_clock));
    BUFG board_clock_global(.I(input_clock),.O(root_clk));
    reg [7:0] poweron=0;
    reg mmcm_reset=1;
    always @(posedge root_clk) begin
        if (!(&poweron)) poweron<=poweron+1'b1;
        else mmcm_reset<=0;
    end
    MMCME2_BASE #(.CLKIN1_PERIOD(10.000),.DIVCLK_DIVIDE(1),.CLKFBOUT_MULT_F(10.000),
        .CLKOUT0_DIVIDE_F(5.000),.STARTUP_WAIT("FALSE")) reference_clock(
        .CLKIN1(root_clk),.CLKFBIN(feedback_global),.CLKFBOUT(feedback),.CLKOUT0(ref200_unbuffered),
        .LOCKED(ref_locked),.RST(mmcm_reset),.PWRDWN(1'b0));
    BUFG ref_feedback(.I(feedback),.O(feedback_global));
    BUFG ref_global(.I(ref200_unbuffered),.O(ref200));
    (* ASYNC_REG="TRUE" *) reg [2:0] lock_sync=0;
    reg [10:0] settle=0;
    always @(posedge root_clk) begin
        lock_sync<={lock_sync[1:0],ref_locked};
        if (!lock_sync[2]) settle<=0;
        else if (!(&settle)) settle<=settle+1'b1;
    end
    reg mig_resetn=0;
    always @(posedge root_clk or negedge ref_locked)
        if(!ref_locked) mig_resetn<=0;
        else mig_resetn<=&settle;
    // Rev A manual section 13.2: the sink buffer is disabled by default.
    // Enable its FPGA-facing transmitter only after the reference settles.
    assign hdmi_rx_txen=mig_resetn;
    wire ui_clk,mig_reset,mmcm_locked,init_calib_complete,pixel_clk;
    (* ASYNC_REG="TRUE" *) reg [1:0] reset_release=0;
    wire reset_request=mig_reset||!mig_resetn||!mmcm_locked;
    always @(posedge ui_clk or posedge reset_request)
        if(reset_request)reset_release<=0;else reset_release<={reset_release[0],1'b1};
    wire axi_resetn=reset_release[1];
    reg resetn=0;
    always @(posedge ui_clk or posedge reset_request)
        if(reset_request)resetn<=0;else resetn<=init_calib_complete&&axi_resetn;
    wire pixel_reset;
    xpm_cdc_async_rst #(.DEST_SYNC_FF(3),.INIT_SYNC_FF(1),.RST_ACTIVE_HIGH(1)) pixel_reset_cdc(
        .src_arst(!resetn),.dest_clk(pixel_clk),.dest_arst(pixel_reset));
    // XPM FIFO reset/control must assert on a pixel-clock edge. The preceding
    // asynchronous synchronizer handles absent clocks; this final FF gives the
    // RAM controls a synchronous assertion as well as synchronous release.
    reg pixel_reset_sync=1;
    always @(posedge pixel_clk)pixel_reset_sync<=pixel_reset;
    wire pixel_resetn=!pixel_reset_sync;
    wire [1:0] memory_bid,memory_rid;
    wire [31:0] m_axi_mm2s_araddr;
    wire [7:0] m_axi_mm2s_arlen;
    wire [2:0] m_axi_mm2s_arsize;
    wire [1:0] m_axi_mm2s_arburst;
    wire [2:0] m_axi_mm2s_arprot;
    wire [3:0] m_axi_mm2s_arcache;
    wire m_axi_mm2s_arvalid;
    wire m_axi_mm2s_arready;
    wire [127:0] m_axi_mm2s_rdata;
    wire [1:0] m_axi_mm2s_rresp;
    wire m_axi_mm2s_rlast;
    wire m_axi_mm2s_rvalid;
    wire m_axi_mm2s_rready;
    wire [31:0] m_axi_s2mm_awaddr;
    wire [7:0] m_axi_s2mm_awlen;
    wire [2:0] m_axi_s2mm_awsize;
    wire [1:0] m_axi_s2mm_awburst;
    wire [2:0] m_axi_s2mm_awprot;
    wire [3:0] m_axi_s2mm_awcache;
    wire m_axi_s2mm_awvalid;
    wire m_axi_s2mm_awready;
    wire [127:0] m_axi_s2mm_wdata;
    wire [15:0] m_axi_s2mm_wstrb;
    wire m_axi_s2mm_wlast;
    wire m_axi_s2mm_wvalid;
    wire m_axi_s2mm_wready;
    wire [1:0] m_axi_s2mm_bresp;
    wire m_axi_s2mm_bvalid;
    wire m_axi_s2mm_bready;
    n3_mig memory(
        .sys_clk_i(root_clk),.clk_ref_i(ref200),.sys_rst(mig_resetn),
        .ddr3_dq(ddr3_dq),.ddr3_dqs_n(ddr3_dqs_n),.ddr3_dqs_p(ddr3_dqs_p),
        .ddr3_addr(ddr3_addr),.ddr3_ba(ddr3_ba),.ddr3_ras_n(ddr3_ras_n),
        .ddr3_cas_n(ddr3_cas_n),.ddr3_we_n(ddr3_we_n),.ddr3_reset_n(ddr3_reset_n),
        .ddr3_ck_p(ddr3_ck_p),.ddr3_ck_n(ddr3_ck_n),.ddr3_cke(ddr3_cke),.ddr3_dm(ddr3_dm),.ddr3_odt(ddr3_odt),
        .ui_clk(ui_clk),.ui_clk_sync_rst(mig_reset),.mmcm_locked(mmcm_locked),
        .ui_addn_clk_0(),.ui_addn_clk_1(),.ui_addn_clk_2(),.ui_addn_clk_3(),.ui_addn_clk_4(),
        .app_sr_active(),.app_ref_ack(),.app_zq_ack(),.device_temp(),
        .init_calib_complete(init_calib_complete),.aresetn(axi_resetn),
        .app_sr_req(1'b0),.app_ref_req(1'b0),.app_zq_req(1'b0),
        .s_axi_awid(2'b0),.s_axi_awaddr(m_axi_s2mm_awaddr[28:0]),.s_axi_awlen(m_axi_s2mm_awlen),.s_axi_awsize(m_axi_s2mm_awsize),.s_axi_awburst(m_axi_s2mm_awburst),
        .s_axi_awlock(1'b0),.s_axi_awcache(4'b0011),.s_axi_awprot(3'b0),.s_axi_awqos(4'b0),
        .s_axi_awvalid(m_axi_s2mm_awvalid),.s_axi_awready(m_axi_s2mm_awready),.s_axi_wdata(m_axi_s2mm_wdata),.s_axi_wstrb(m_axi_s2mm_wstrb),
        .s_axi_wlast(m_axi_s2mm_wlast),.s_axi_wvalid(m_axi_s2mm_wvalid),.s_axi_wready(m_axi_s2mm_wready),
        .s_axi_bid(memory_bid),.s_axi_bresp(m_axi_s2mm_bresp),.s_axi_bvalid(m_axi_s2mm_bvalid),.s_axi_bready(m_axi_s2mm_bready),
        .s_axi_arid(2'b0),.s_axi_araddr(m_axi_mm2s_araddr[28:0]),.s_axi_arlen(m_axi_mm2s_arlen),.s_axi_arsize(m_axi_mm2s_arsize),.s_axi_arburst(m_axi_mm2s_arburst),
        .s_axi_arlock(1'b0),.s_axi_arcache(4'b0011),.s_axi_arprot(3'b0),.s_axi_arqos(4'b0),
        .s_axi_arvalid(m_axi_mm2s_arvalid),.s_axi_arready(m_axi_mm2s_arready),.s_axi_rid(memory_rid),.s_axi_rdata(m_axi_mm2s_rdata),
        .s_axi_rresp(m_axi_mm2s_rresp),.s_axi_rlast(m_axi_mm2s_rlast),.s_axi_rvalid(m_axi_mm2s_rvalid),.s_axi_rready(m_axi_mm2s_rready)
    );
    localparam integer STATUS_BITS=960+1664*BUSINESS_IDENTITY;
    wire [72:0] dp_data;wire dp_ready;wire [575+1664*BUSINESS_IDENTITY:0] ui_status;
    wire link_ready,physical_video;wire [7:0] link_kind,link_debug;wire [63:0] link_payload;
    wire rx_clk,rx_locked_raw,rx_de,rx_hs,rx_vs;wire [23:0] rx_rbg;
    // Upstream pLocked has asynchronous assertion of loss. Register it once
    // before it can control ingress BRAM enables; the independent UI watchdog
    // still detects an absent RX clock even when this register cannot advance.
    reg rx_locked=0;
    always @(posedge rx_clk)rx_locked<=rx_locked_raw;
    wire rx_reset;reg rx_reset_sync=1;wire rx_resetn=!rx_reset_sync;
    xpm_cdc_async_rst #(.DEST_SYNC_FF(3),.INIT_SYNC_FF(1),.RST_ACTIVE_HIGH(1)) rx_reset_cdc(
        .src_arst(!resetn),.dest_clk(rx_clk),.dest_arst(rx_reset));
    always @(posedge rx_clk)rx_reset_sync<=rx_reset;
    hdmi_receiver #(.EDID_FILE(EDID_FILE)) hdmi(.ref200(ref200),.ref_ready(mig_resetn),
        .tmds_clk_p(hdmi_clk_p),.tmds_clk_n(hdmi_clk_n),.tmds_data_p(hdmi_data_p),.tmds_data_n(hdmi_data_n),
        .ddc_scl(hdmi_scl),.ddc_sda(hdmi_sda),.hpd(hdmi_hpd),.pixel_clk(rx_clk),.locked(rx_locked_raw),
        .de(rx_de),.hs(rx_hs),.vs(rx_vs),.rbg(rx_rbg));
    wire [383:0] input_status;
    bridge_core #(.CANDIDATE_ID(CANDIDATE_ID),.BUSINESS_IDENTITY(BUSINESS_IDENTITY)) core(.*);
    dp_framebuffer_link_continuous link(.clk100(root_clk),.stream_data(dp_data),.stream_ready(dp_ready),
        .symbol_clock(pixel_clk),.link_ready(link_ready),.video_active(physical_video),.link_kind(link_kind),.link_payload(link_payload),
        .debug(link_debug),.diagnostic_tx(),.*);
    wire [STATUS_BITS+191:0] telemetry;
    wire [STATUS_BITS-1:0] monitor_status;
    generate if(BUSINESS_IDENTITY)begin
        assign monitor_status={ui_status[2239:576],input_status,ui_status[575:0]};
    end else begin
        assign monitor_status={input_status,ui_status};
    end endgenerate
    bridge_telemetry #(.STATUS_BITS(STATUS_BITS)) monitor(.ui_clk(ui_clk),.root_clk(root_clk),.ui_status(monitor_status),
        .ref_locked(ref_locked),.mig_resetn(mig_resetn),.ui_reset(!axi_resetn),.calib(init_calib_complete),.payload(telemetry));
    // The appended link record is already synchronous to root_clk. The UART
    // snapshots all 112 bytes together; UI data has its independent age/sequence.
    wire [127:0] link_record={24'b0,link_debug[6:0],link_ready,link_payload,24'b0,link_kind};
    wire [1279+1664*BUSINESS_IDENTITY:0] payload;
    // SRB5 keeps all forty SRB4 words in place, followed by 52 identity words.
    generate if(BUSINESS_IDENTITY)begin
        assign payload={telemetry[2623:960],link_record,telemetry[2815:2624],telemetry[959:0]};
    end else begin
        assign payload={link_record,telemetry};
    end endgenerate
    bridge_uart #(.BUSINESS_IDENTITY(BUSINESS_IDENTITY)) serial(.clk(root_clk),.payload(payload),.tx(diagnostic_tx));
    assign led={link_ready,telemetry[STATUS_BITS+166-:3],telemetry[67:64]};
endmodule
