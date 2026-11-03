// N4 external-input scheduler; source discontinuities retire all leases before epoch change.
// Runtime errors fail closed; this block does not reset active AXI masters.
// resetn may only be asserted together with memory masters and pixel FIFOs.
module bridge_pipeline_control #(
    parameter integer PRODUCE_CYCLES=3333333,READ_WINDOW_CYCLES=50000,
    parameter integer FRAME_BYTES=8294400, BUSINESS_IDENTITY=0
)(
    input wire ui_clk,pixel_clk,resetn,pixel_resetn,
    input wire source_offer,source_discontinuity,producer_good,
    input wire [31:0] producer_crc,output wire source_accept,
    input wire marker_valid,marker_invalid,input wire [80:0] marker_identity,
    output wire [1663:0] sr_status,
    output reg [31:0] expected_crc=0,
    input wire prepare,boundary,external_active,frame_complete,frame_failed,scan_link_valid,
    input wire fifo_prefilled,fifo_empty,fifo_reset_busy,fifo_received,fifo_retired,fifo_bad,
    output reg fifo_start=0,fifo_abort=0,fifo_release=0,
    output wire external_enable,output reg mm2s_fsync=0,
    output reg [31:0] expected_frame=0,
    input wire wr_ready,rd_ready,wr_armed,rd_armed,wr_done,rd_done,control_fault,
    output reg wr_start=0,rd_start=0,output reg [31:0] wr_base=0,rd_base=0,
    output wire wr_complete,rd_complete,
    output reg producer_start=0,output reg [15:0] producer_frame=0,
    input wire producer_done,producer_fault,
    output reg fault=0,output wire [17:0] owner_states,
    output reg [31:0] source_frames=0,written_frames=0,read_frames=0,
    output reg [31:0] displayed_frames=0,new_frames=0,repeated_frames=0,dropped_frames=0,
    output reg [31:0] max_latency_cycles=0,last_displayed_frame=0,
    output wire [31:0] epoch,output reg [63:0] ticks=0
);
    wire prepare_ui,commit_ui,cancel_ui,scan_ui,retired_ui;
    reg commit_px=0,cancel_px=0,scan_px=0;
    wire [1:0] pixel_flags_ui;
    xpm_cdc_array_single #(.WIDTH(2),.DEST_SYNC_FF(3),.SRC_INPUT_REG(1),.INIT_SYNC_FF(1)) flags(
        .src_clk(pixel_clk),.src_in({fifo_received,fifo_bad||frame_failed}),.dest_clk(ui_clk),.dest_out(pixel_flags_ui));
    xpm_cdc_pulse #(.DEST_SYNC_FF(3),.INIT_SYNC_FF(1),.REG_OUTPUT(1),.RST_USED(1)) prep_cdc(
        .src_clk(pixel_clk),.src_rst(!pixel_resetn),.src_pulse(prepare),.dest_clk(ui_clk),.dest_rst(!resetn),.dest_pulse(prepare_ui));
    xpm_cdc_pulse #(.DEST_SYNC_FF(3),.INIT_SYNC_FF(1),.REG_OUTPUT(1),.RST_USED(1)) commit_cdc(
        .src_clk(pixel_clk),.src_rst(!pixel_resetn),.src_pulse(commit_px),.dest_clk(ui_clk),.dest_rst(!resetn),.dest_pulse(commit_ui));
    xpm_cdc_pulse #(.DEST_SYNC_FF(3),.INIT_SYNC_FF(1),.REG_OUTPUT(1),.RST_USED(1)) cancel_cdc(
        .src_clk(pixel_clk),.src_rst(!pixel_resetn),.src_pulse(cancel_px),.dest_clk(ui_clk),.dest_rst(!resetn),.dest_pulse(cancel_ui));
    xpm_cdc_pulse #(.DEST_SYNC_FF(3),.INIT_SYNC_FF(1),.REG_OUTPUT(1),.RST_USED(1)) scan_cdc(
        .src_clk(pixel_clk),.src_rst(!pixel_resetn),.src_pulse(scan_px),.dest_clk(ui_clk),.dest_rst(!resetn),.dest_pulse(scan_ui));
    xpm_cdc_pulse #(.DEST_SYNC_FF(3),.INIT_SYNC_FF(1),.REG_OUTPUT(1),.RST_USED(1)) retire_cdc(
        .src_clk(pixel_clk),.src_rst(!pixel_resetn),.src_pulse(fifo_retired),.dest_clk(ui_clk),.dest_rst(!resetn),.dest_pulse(retired_ui));
    reg [31:0] produce_count=0;
    reg [31:0] read_window=0;
    wire produce_tick=source_offer;
    reg invalidate_pending=0,writer_good=0;reg [31:0] crc_by_buffer[0:2];
    reg [31:0] reader_crc=0;

    reg writer_active=0,reader_active=0,writer_begun=0,reader_sent=0;
    reg write_stream_done=0,write_publish=0,read_dma_done=0,read_pixels_done=0;
    reg [1:0] writer_index=0,reader_index=0;
    reg [31:0] reader_frame=0,reader_epoch=0,last_epoch=0;
    reg [63:0] reader_time=0;
    reg ever_displayed=0;
    reg [2:0] refs;
    always_comb begin
        refs=0;
        if(writer_active)refs[writer_index]=1;
        if(reader_active)refs[reader_index]=1;
    end
    wire owner_wr_ready,owner_rd_ready,owner_fault,owner_pending;
    wire [1:0] owner_wr_index,owner_rd_index;
    wire [31:0] owner_rd_frame,owner_rd_epoch;
    wire [63:0] owner_rd_time;
    wire invalidate_now=invalidate_pending&&!writer_active&&!reader_active&&!write_publish&&!owner_pending;
    wire claim_write=produce_tick&&wr_ready&&!writer_active&&!write_publish&&owner_wr_ready&&!fault&&!invalidate_pending&&!source_discontinuity;
    assign source_accept=claim_write;
    wire claim_read=read_window!=0&&rd_ready&&!reader_active&&owner_rd_ready&&!fault&&!invalidate_pending&&!source_discontinuity;
    bridge_frame_owner owner(.clk(ui_clk),.resetn(resetn),.invalidate(invalidate_now),.ref_busy(refs),
        .wr_request(claim_write),.source_frame(source_frames),.source_time(ticks),
        .wr_ready(owner_wr_ready),.wr_index(owner_wr_index),.current_epoch(epoch),
        .wr_done(write_publish),.wr_ok(writer_good&&!fault&&!invalidate_pending),.rd_request(claim_read),
        .rd_ready(owner_rd_ready),.rd_index(owner_rd_index),.rd_frame(owner_rd_frame),
        .rd_epoch(owner_rd_epoch),.rd_time(owner_rd_time),.rd_commit(commit_ui),.rd_cancel(cancel_ui),
        .pending(owner_pending),.fault(owner_fault),.states(owner_states));
    generate if(BUSINESS_IDENTITY)begin:business_accounting
        sr_identity_tracker tracker(.clk(ui_clk),.resetn(resetn),.invalidate(invalidate_now),.ticks(ticks),
            .claim_write(claim_write),.write_index(owner_wr_index),.marker_valid(marker_valid),
            .marker_invalid(marker_invalid),.marker_identity(marker_identity),
            .publish(write_publish),.publish_good(writer_good&&!fault&&!invalidate_pending),
            .claim_read(claim_read),.read_index(owner_rd_index),.read_time(owner_rd_time),
            .scan_complete(scan_ui&&!fault&&!control_fault&&!pixel_flags_ui[0]&&reader_active&&reader_sent),
            .read_release(reader_active&&read_dma_done&&read_pixels_done),.status(sr_status));
    end else begin:no_business_accounting
        assign sr_status=0;
    end endgenerate
    assign wr_complete=write_stream_done;
    assign rd_complete=pixel_flags_ui[1];
    // Metadata stays held until the four-phase handshake completes. There is
    // only one read lease; event pulses refer to that lease, never a sampled bus.
    reg [63:0] descriptor=0;reg send=0;reg [1:0] send_state=0;
    wire acknowledged,descriptor_valid;wire [63:0] descriptor_px;
    xpm_cdc_handshake #(.WIDTH(64),.DEST_EXT_HSK(0),.DEST_SYNC_FF(3),.SRC_SYNC_FF(3),
        .INIT_SYNC_FF(1),.SIM_ASSERT_CHK(1)) descriptor_cdc(
        .src_clk(ui_clk),.src_in(descriptor),.src_send(send),.src_rcv(acknowledged),
        .dest_clk(pixel_clk),.dest_out(descriptor_px),.dest_req(descriptor_valid),.dest_ack(1'b0));
    // Register the two independent drop events before the wide accumulator.
    // This removes owner selection plus two cascaded 32-bit additions from
    // the grant path; telemetry is delayed by one UI cycle only.
    reg [1:0] drop_increment=0;
    always @(posedge ui_clk)begin
        wr_start<=0;rd_start<=0;producer_start<=0;write_publish<=0;
        if(!resetn)begin
            fault<=0;produce_count<=0;read_window<=0;writer_active<=0;reader_active<=0;writer_begun<=0;reader_sent<=0;
            write_stream_done<=0;read_dma_done<=0;read_pixels_done<=0;
            send<=0;send_state<=0;source_frames<=0;written_frames<=0;read_frames<=0;
            displayed_frames<=0;new_frames<=0;repeated_frames<=0;dropped_frames<=0;drop_increment<=0;
            invalidate_pending<=0;writer_good<=0;reader_crc<=0;
            for(integer c=0;c<3;c=c+1)crc_by_buffer[c]<=0;
            ticks<=0;max_latency_cycles<=0;last_displayed_frame<=0;last_epoch<=0;ever_displayed<=0;
        end else begin
            ticks<=ticks+1'b1;
            if(invalidate_now)invalidate_pending<=0;
            if(source_discontinuity||(producer_done&&!producer_good))invalidate_pending<=1;
            if(prepare_ui)read_window<=READ_WINDOW_CYCLES;
            else if(read_window!=0)read_window<=read_window-1'b1;
            if(control_fault||producer_fault||owner_fault||pixel_flags_ui[0])fault<=1;
            drop_increment<={1'b0,(produce_tick&&!claim_write)}+{1'b0,(producer_done&&!producer_good)};
            dropped_frames<=dropped_frames+drop_increment;
            if(produce_tick)begin
                produce_count<=0;source_frames<=source_frames+1'b1;

            end else produce_count<=produce_count+1'b1;
            if(claim_write)begin
                writer_active<=1;writer_index<=owner_wr_index;wr_base<=owner_wr_index*FRAME_BYTES;
                producer_frame<=source_frames[15:0];writer_good<=0;wr_start<=1;writer_begun<=0;write_stream_done<=0;
            end
            if(writer_active&&wr_armed&&!writer_begun&&!fault)begin producer_start<=1;writer_begun<=1;end
            if(producer_done)begin
                write_stream_done<=1;writer_good<=producer_good;
                crc_by_buffer[writer_index]<=producer_crc;

            end
            if(wr_done)begin
                if(!writer_active||!write_stream_done)fault<=1;
                else begin writer_active<=0;write_publish<=1;if(writer_good&&!invalidate_pending)written_frames<=written_frames+1'b1;end
            end
            if(claim_read)begin
                read_window<=0;
                reader_active<=1;reader_index<=owner_rd_index;rd_base<=owner_rd_index*FRAME_BYTES;
                reader_frame<=owner_rd_frame;reader_crc<=crc_by_buffer[owner_rd_index];reader_epoch<=owner_rd_epoch;reader_time<=owner_rd_time;
                rd_start<=1;reader_sent<=0;read_dma_done<=0;read_pixels_done<=0;
            end
            case(send_state)
                0:if(reader_active&&rd_armed&&!reader_sent&&!fault)begin
                    descriptor<={reader_crc,reader_frame};reader_sent<=1;send_state<=1;
                end
                1:begin send<=1;send_state<=2;end
                2:if(acknowledged)begin send<=0;send_state<=3;end
                3:if(!acknowledged)send_state<=0;
            endcase
            if(rd_done)begin
                if(!reader_active)fault<=1;
                else begin read_dma_done<=1;read_frames<=read_frames+1'b1;end
            end
            if(retired_ui)begin
                if(!reader_active)fault<=1;else read_pixels_done<=1;
            end
            if(reader_active&&read_dma_done&&read_pixels_done)reader_active<=0;
            if(scan_ui&&!fault&&!control_fault&&!pixel_flags_ui[0])begin
                if(!reader_active||!reader_sent)fault<=1;
                else begin
                    displayed_frames<=displayed_frames+1'b1;
                    if(ever_displayed&&reader_frame==last_displayed_frame&&reader_epoch==last_epoch)
                        repeated_frames<=repeated_frames+1'b1;
                    else begin
                        new_frames<=new_frames+1'b1;
                        if(ticks-reader_time>max_latency_cycles)max_latency_cycles<=ticks-reader_time;
                    end
                    ever_displayed<=1;last_displayed_frame<=reader_frame;last_epoch<=reader_epoch;
                end
            end
        end
    end
    wire fault_px;
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(0),.INIT_SYNC_FF(1)) fault_cdc(
        .src_clk(1'b0),.src_in(fault),.dest_clk(pixel_clk),.dest_out(fault_px));
    reg pending_px=0,active_px=0,aborting_px=0;
    reg [3:0] sync_count=0;
    assign external_enable=pending_px&&fifo_prefilled&&!fifo_bad&&!fault_px;
    always @(posedge pixel_clk)begin
        fifo_start<=0;fifo_abort<=0;fifo_release<=0;commit_px<=0;cancel_px<=0;scan_px<=0;
        if(!pixel_resetn)begin
            pending_px<=0;active_px<=0;aborting_px<=0;mm2s_fsync<=0;sync_count<=0;expected_frame<=0;expected_crc<=0;
        end else begin
            if(descriptor_valid)begin
                if(pending_px||active_px||!fifo_empty||fifo_reset_busy)fifo_abort<=1;
                else begin
                    expected_frame<=descriptor_px[31:0];expected_crc<=descriptor_px[63:32];fifo_start<=1;pending_px<=1;
                    mm2s_fsync<=1;sync_count<=8;aborting_px<=0;
                end
            end
            if(sync_count!=0)begin sync_count<=sync_count-1'b1;if(sync_count==1)mm2s_fsync<=0;end
            if(boundary&&pending_px)begin
                pending_px<=0;
                if(external_active)begin active_px<=1;commit_px<=1;end
                else begin cancel_px<=1;fifo_abort<=1;aborting_px<=1;end
            end
            if((frame_failed||fifo_bad||fault_px)&&(pending_px||active_px)&&!aborting_px)begin
                fifo_abort<=1;aborting_px<=1;
            end
            if(prepare&&active_px)begin
                active_px<=0;
                if(frame_complete&&!frame_failed&&!fifo_bad&&!aborting_px&&fifo_received&&fifo_empty)begin
                    fifo_release<=1;scan_px<=scan_link_valid;
                end else if(!aborting_px)begin fifo_abort<=1;aborting_px<=1;end
            end
        end
    end
endmodule
