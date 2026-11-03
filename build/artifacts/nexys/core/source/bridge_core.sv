// Shared 128-bit AXI read/write channels connect directly to one MIG slave.
// MM2S and S2MM never drive the same AXI direction; all transactions use ID 0.
module bridge_core #(
    parameter [31:0] CANDIDATE_ID=32'h26100601,
    parameter integer BUSINESS_IDENTITY=0
)(
    input wire ui_clk,pixel_clk,resetn,pixel_resetn,physical_video,
    input wire rx_clk,rx_resetn,rx_locked,rx_de,rx_hs,rx_vs,input wire [23:0] rx_rbg,
    output wire [383:0] input_status,
    output wire [72:0] dp_data,output wire dp_ready,
    output wire [575+1664*BUSINESS_IDENTITY:0] ui_status,
    input wire [1:0] memory_bid,memory_rid,
    output wire [31:0] m_axi_mm2s_araddr,
    output wire [7:0] m_axi_mm2s_arlen,
    output wire [2:0] m_axi_mm2s_arsize,
    output wire [1:0] m_axi_mm2s_arburst,
    output wire [2:0] m_axi_mm2s_arprot,
    output wire [3:0] m_axi_mm2s_arcache,
    output wire m_axi_mm2s_arvalid,
    input wire m_axi_mm2s_arready,
    input wire [127:0] m_axi_mm2s_rdata,
    input wire [1:0] m_axi_mm2s_rresp,
    input wire m_axi_mm2s_rlast,
    input wire m_axi_mm2s_rvalid,
    output wire m_axi_mm2s_rready,
    output wire [31:0] m_axi_s2mm_awaddr,
    output wire [7:0] m_axi_s2mm_awlen,
    output wire [2:0] m_axi_s2mm_awsize,
    output wire [1:0] m_axi_s2mm_awburst,
    output wire [2:0] m_axi_s2mm_awprot,
    output wire [3:0] m_axi_s2mm_awcache,
    output wire m_axi_s2mm_awvalid,
    input wire m_axi_s2mm_awready,
    output wire [127:0] m_axi_s2mm_wdata,
    output wire [15:0] m_axi_s2mm_wstrb,
    output wire m_axi_s2mm_wlast,
    output wire m_axi_s2mm_wvalid,
    input wire m_axi_s2mm_wready,
    input wire [1:0] m_axi_s2mm_bresp,
    input wire m_axi_s2mm_bvalid,
    output wire m_axi_s2mm_bready
);
    wire s_axi_lite_aclk;
    wire m_axis_mm2s_aclk;
    wire s_axis_s2mm_aclk;
    wire axi_resetn;
    wire s_axi_lite_awvalid;
    wire s_axi_lite_awready;
    wire [8:0] s_axi_lite_awaddr;
    wire s_axi_lite_wvalid;
    wire s_axi_lite_wready;
    wire [31:0] s_axi_lite_wdata;
    wire [1:0] s_axi_lite_bresp;
    wire s_axi_lite_bvalid;
    wire s_axi_lite_bready;
    wire s_axi_lite_arvalid;
    wire s_axi_lite_arready;
    wire [8:0] s_axi_lite_araddr;
    wire s_axi_lite_rvalid;
    wire s_axi_lite_rready;
    wire [31:0] s_axi_lite_rdata;
    wire [1:0] s_axi_lite_rresp;
    wire mm2s_fsync;
    wire [5:0] mm2s_frame_ptr_out;
    wire [5:0] s2mm_frame_ptr_out;
    wire mm2s_buffer_empty;
    wire mm2s_buffer_almost_empty;
    wire s2mm_buffer_full;
    wire s2mm_buffer_almost_full;
    wire [63:0] m_axis_mm2s_tdata;
    wire [7:0] m_axis_mm2s_tkeep;
    wire [0:0] m_axis_mm2s_tuser;
    wire m_axis_mm2s_tvalid;
    wire m_axis_mm2s_tready;
    wire m_axis_mm2s_tlast;
    wire [31:0] s_axis_s2mm_tdata;
    wire [3:0] s_axis_s2mm_tkeep;
    wire [0:0] s_axis_s2mm_tuser;
    wire s_axis_s2mm_tvalid;
    wire s_axis_s2mm_tready;
    wire s_axis_s2mm_tlast;
    wire mm2s_introut;
    wire s2mm_introut;
    assign s_axi_lite_aclk=ui_clk;
    assign m_axis_mm2s_aclk=pixel_clk;
    assign s_axis_s2mm_aclk=ui_clk;
    assign axi_resetn=resetn;
    n3_vdma dma(.m_axi_mm2s_aclk(ui_clk),.m_axi_s2mm_aclk(ui_clk),.*);
    wire wr_start,rd_start,wr_complete,rd_complete,wr_ready,rd_ready,wr_armed,rd_armed,wr_done,rd_done;
    wire [31:0] wr_base,rd_base,last_status;
    wire control_fault,wr_fence_complete,rd_fence_complete,wr_fence_fault,rd_fence_fault;
    wire [31:0] words_written,words_read;
    wire address_fault=(m_axi_s2mm_awvalid&&(|m_axi_s2mm_awaddr[31:29])) ||
        (m_axi_mm2s_arvalid&&(|m_axi_mm2s_araddr[31:29]));
    wire identity_fault=(m_axi_s2mm_bvalid&&memory_bid!=0)||(m_axi_mm2s_rvalid&&memory_rid!=0);
    wire wr_drained=wr_fence_complete&&!m_axi_s2mm_awvalid&&!m_axi_s2mm_wvalid&&!m_axi_s2mm_bvalid;
    wire rd_drained=rd_fence_complete&&!m_axi_mm2s_arvalid&&!m_axi_mm2s_rvalid;
    vdma_axi_fence fence(.clk(ui_clk),.resetn(resetn),.wr_complete(wr_fence_complete),
        .rd_complete(rd_fence_complete),.wr_fault(wr_fence_fault),.rd_fault(rd_fence_fault),.*);
    vdma_frame_control control(.clk(ui_clk),.resetn(resetn),
        .wr_bad(wr_fence_fault||address_fault||identity_fault),.rd_bad(rd_fence_fault||address_fault||identity_fault),
        .fault(control_fault),.awaddr(s_axi_lite_awaddr),.araddr(s_axi_lite_araddr),.wdata(s_axi_lite_wdata),
        .awvalid(s_axi_lite_awvalid),.wvalid(s_axi_lite_wvalid),.bready(s_axi_lite_bready),
        .arvalid(s_axi_lite_arvalid),.rready(s_axi_lite_rready),
        .awready(s_axi_lite_awready),.wready(s_axi_lite_wready),.bvalid(s_axi_lite_bvalid),
        .arready(s_axi_lite_arready),.rvalid(s_axi_lite_rvalid),.bresp(s_axi_lite_bresp),
        .rresp(s_axi_lite_rresp),.rdata(s_axi_lite_rdata),.*);
    wire producer_start,producer_done,producer_fault,producer_busy;
    wire [15:0] producer_frame;
    wire source_offer,source_accept,source_discontinuity,source_present,producer_good;
    wire [31:0] producer_crc,expected_crc;
    wire [35:0] token;wire token_valid,token_pop,flush_busy;
    wire px_valid,px_sof,px_eol,rx_frame_end,rx_frame_good,rx_frame_bad;
    wire [31:0] rgbx,rx_frames,rx_good,rx_bad,rx_pixels,rx_lines,rx_hperiod,rx_vperiod;
    wire [31:0] loss_events,overflow_events,accepted,rejected,damaged;
    hdmi_geometry geometry(.clk(rx_clk),.resetn(rx_resetn),.locked(rx_locked),
        .de(rx_de),.hs(rx_hs),.vs(rx_vs),.rbg(rx_rbg),.pixel_valid(px_valid),.sof(px_sof),.eol(px_eol),
        .rgbx(rgbx),.frame_end(rx_frame_end),.frame_good(rx_frame_good),.frame_bad(rx_frame_bad),
        .frames(rx_frames),.good_frames(rx_good),.bad_frames(rx_bad),.last_pixels(rx_pixels),
        .last_lines(rx_lines),.last_hperiod(rx_hperiod),.last_vperiod(rx_vperiod));
    hdmi_token_cdc ingress_fifo(.rx_clk(rx_clk),.ui_clk(ui_clk),.resetn(resetn),.rx_locked(rx_locked),
        .pixel_valid(px_valid),.sof(px_sof),.eol(px_eol),.frame_end(rx_frame_end),.frame_good(rx_frame_good),
        .rgbx(rgbx),.token(token),.token_valid(token_valid),.token_pop(token_pop),
        .source_present(source_present),.discontinuity(source_discontinuity),
        .loss_events(loss_events),.overflow_events(overflow_events),.flush_busy(flush_busy));
    hdmi_frame_input producer(.clk(ui_clk),.resetn(resetn),.source_present(source_present),
        .discontinuity(source_discontinuity),.token(token),.token_valid(token_valid),.token_pop(token_pop),
        .offer(source_offer),.accept(source_accept),.start(producer_start),.done(producer_done),
        .good(producer_good),.active(producer_busy),.frame_crc(producer_crc),
        .accepted(accepted),.rejected(rejected),.damaged(damaged),
        .tdata(s_axis_s2mm_tdata),.tkeep(s_axis_s2mm_tkeep),.tvalid(s_axis_s2mm_tvalid),
        .tuser(s_axis_s2mm_tuser),.tlast(s_axis_s2mm_tlast),.tready(s_axis_s2mm_tready));
    assign producer_fault=1'b0; // source loss is recoverable, not an AXI failure
    wire marker_valid,marker_invalid;wire [80:0] marker_identity;wire [1663:0] sr_status;
    generate if(BUSINESS_IDENTITY)begin:business_marker
        sr_identity_marker_rx marker(.clk(ui_clk),.resetn(resetn),
            .pixel_valid(s_axis_s2mm_tvalid&&s_axis_s2mm_tready),.sof(s_axis_s2mm_tuser[0]),
            .eol(s_axis_s2mm_tlast),.rgbx(s_axis_s2mm_tdata),
            .frame_end(producer_done),.frame_good(producer_good),.valid(marker_valid),.invalid(marker_invalid),
            .has_result(marker_identity[80]),.mode(marker_identity[79:72]),.scale(marker_identity[71:64]),
            .generation(marker_identity[63:32]),.source_sequence(marker_identity[31:0]));
    end else begin:no_business_marker
        assign marker_valid=0;assign marker_invalid=0;assign marker_identity=0;
    end endgenerate
    wire [191:0] rx_snapshot;
    atomic_status_cdc #(.WIDTH(192)) rx_stats(.src_clk(rx_clk),.dest_clk(ui_clk),
        .src_resetn(rx_resetn),.dest_resetn(resetn),
        .status({rx_vperiod,rx_hperiod,rx_pixels,rx_bad,rx_good,rx_frames}),.snapshot(rx_snapshot));
    assign input_status={rx_snapshot,damaged,rejected,accepted,overflow_events,loss_events,30'b0,flush_busy,source_present};
    wire prepare,boundary,frame_complete,frame_failed,external_active,external_enable;
    // Physical video selection is observed in its own pixel-clock domain,
    // after the link's idle-to-video boundary switch, not merely AUX ready.
    reg scan_link_valid=0;
    always @(posedge pixel_clk)begin
        if(!pixel_resetn)scan_link_valid<=0;
        else if(boundary)scan_link_valid<=physical_video;
        else if(!physical_video)scan_link_valid<=0;
    end
    wire fifo_start,fifo_abort,fifo_release,fifo_prefilled,fifo_empty,fifo_reset_busy,fifo_received,fifo_retired;
    wire fifo_bad_raw,fifo_bad,fifo_busy,pair_valid,pair_pop,fifo_ready,check_ready;
    wire [47:0] pair_data;wire [31:0] expected_frame,pixels_received,pairs_consumed;
    assign m_axis_mm2s_tready=fifo_ready&&check_ready;
    video_pair_fifo #(.PIXELS_PER_BEAT(2)) pixels(.clk(pixel_clk),.resetn(pixel_resetn),.start(fifo_start),.abort(fifo_abort),.release_frame(fifo_release),
        .tdata(m_axis_mm2s_tdata),.tkeep(m_axis_mm2s_tkeep),.tvalid(m_axis_mm2s_tvalid&&check_ready),
        .tuser(m_axis_mm2s_tuser),.tlast(m_axis_mm2s_tlast),.tready(fifo_ready),
        .pair_data(pair_data),.pair_valid(pair_valid),.pair_pop(pair_pop),.prefilled(fifo_prefilled),
        .empty(fifo_empty),.reset_busy(fifo_reset_busy),.busy(fifo_busy),.received(fifo_received),
        .retired(fifo_retired),.bad(fifo_bad_raw),.pixels_received(pixels_received),.pairs_consumed(pairs_consumed));
    wire check_busy,check_complete,check_bad;
    wire [31:0] checked_pixels;wire [63:0] error_expected,error_observed;
    wire [31:0] observed_crc;
    video_crc_check verifier(.clk(pixel_clk),.resetn(pixel_resetn),.start(fifo_start),.expected_crc(expected_crc),
        .busy(check_busy),.complete(check_complete),.bad(check_bad),.tdata(m_axis_mm2s_tdata),
        .tkeep(m_axis_mm2s_tkeep),.tvalid(m_axis_mm2s_tvalid&&fifo_ready),.tuser(m_axis_mm2s_tuser),
        .tlast(m_axis_mm2s_tlast),.tready(check_ready),.checked_pixels(checked_pixels),.observed_crc(observed_crc));
    assign fifo_bad=fifo_bad_raw||check_bad;
    dp_framebuffer_source #(.transfer_unit_size(64)) stream(.clk(pixel_clk),.reset(!pixel_resetn),
        .pair_data(pair_data),.pair_valid(pair_valid),.pair_pop(pair_pop),.external_enable(external_enable),
        .frame_prepare(prepare),.frame_boundary(boundary),.frame_complete(frame_complete),
        .external_active(external_active),.frame_failed(frame_failed),.stream_channel_count(),.ready(dp_ready),.data(dp_data));
    wire fault;wire [17:0] owner_states;
    wire [31:0] source_frames,written_frames,read_frames,displayed_frames,new_frames,repeated_frames,dropped_frames;
    wire [31:0] max_latency_cycles,last_displayed_frame,epoch;wire [63:0] ticks;
    bridge_pipeline_control #(.BUSINESS_IDENTITY(BUSINESS_IDENTITY)) scheduler(.*);
    reg [31:0] write_beats=0,read_beats=0;
    always @(posedge ui_clk)begin
        if(!resetn)begin write_beats<=0;read_beats<=0;end
        else begin
            if(m_axi_s2mm_wvalid&&m_axi_s2mm_wready)write_beats<=write_beats+1'b1;
            if(m_axi_mm2s_rvalid&&m_axi_mm2s_rready)read_beats<=read_beats+1'b1;
        end
    end
    wire [31:0] flags={25'b0,identity_fault,address_fault,rd_fence_fault,wr_fence_fault,control_fault,producer_fault,fault};
    wire [575:0] legacy_status={ticks,14'b0,owner_states,read_beats,write_beats,epoch,last_displayed_frame,
        max_latency_cycles,dropped_frames,repeated_frames,new_frames,displayed_frames,read_frames,
        written_frames,source_frames,flags,32'h4e344231,CANDIDATE_ID};
    assign ui_status=BUSINESS_IDENTITY ? {sr_status,legacy_status} : legacy_status;
endmodule
