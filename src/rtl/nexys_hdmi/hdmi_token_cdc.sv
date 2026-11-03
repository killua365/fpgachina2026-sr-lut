// RX is never stalled. Overflow/loss establishes a four-phase flush barrier:
// block writes, drain FIFO, acknowledge RX's sticky overflow, then re-arm only
// at a new SOF. No VDMA or DDR reset is performed by this block.
module hdmi_token_cdc #(
    parameter integer DEPTH=4096,LOSS_CYCLES=100000
)(
    input wire rx_clk,ui_clk,resetn,rx_locked,
    input wire pixel_valid,sof,eol,frame_end,frame_good,
    input wire [31:0] rgbx,
    output wire [35:0] token,output wire token_valid,input wire token_pop,
    output reg source_present=0,output reg discontinuity=0,
    output reg [31:0] loss_events=0,overflow_events=0,
    output wire flush_busy
);
    wire wr_busy,rd_busy,full,empty;wire [35:0] fifo_out;
    (* ASYNC_REG="TRUE", SHREG_EXTRACT="NO" *) reg [2:0] rx_reset_chain=3'b111;
    reg fifo_reset=1;
    always @(posedge rx_clk or negedge resetn)
        if(!resetn)rx_reset_chain<=3'b111;
        else rx_reset_chain<={rx_reset_chain[1:0],1'b0};
    // XPM_FIFO_ASYNC requires rst synchronous to wr_clk (UG953). The final
    // register makes assertion synchronous too; absent clocks are handled by
    // the UI loss watchdog and flush barrier, not by resetting a live FIFO.
    always @(posedge rx_clk)fifo_reset<=rx_reset_chain[2];
    wire request=frame_end||pixel_valid;
    reg overflow=0,rx_frame=0;reg [10:0] heartbeat_count=0;
    // Clock lock is not evidence of a valid raster. Reacquisition must see two
    // new complete good frames after the flush barrier is ready. Transfer the
    // sparse frame events as toggles; their minimum separation is a video frame.
    reg good_frame_toggle=0,bad_frame_toggle=0;
    reg flush=0;
    wire flush_rx,overflow_ui,locked_ui,heartbeat_ui,flush_seen,wr_busy_ui;
    wire good_frame_ui,bad_frame_ui;
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(1),.INIT_SYNC_FF(1)) bcdc(
        .src_clk(rx_clk),.src_in(wr_busy),.dest_clk(ui_clk),.dest_out(wr_busy_ui));
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(0),.INIT_SYNC_FF(1)) fcdc(
        .src_clk(1'b0),.src_in(flush),.dest_clk(rx_clk),.dest_out(flush_rx));
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(0),.INIT_SYNC_FF(1)) ocdc(
        .src_clk(1'b0),.src_in(overflow),.dest_clk(ui_clk),.dest_out(overflow_ui));
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(0),.INIT_SYNC_FF(1)) lcdc(
        .src_clk(1'b0),.src_in(rx_locked),.dest_clk(ui_clk),.dest_out(locked_ui));
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(0),.INIT_SYNC_FF(1)) hcdc(
        .src_clk(1'b0),.src_in(heartbeat_count[10]),.dest_clk(ui_clk),.dest_out(heartbeat_ui));
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(0),.INIT_SYNC_FF(1)) gcdc(
        .src_clk(1'b0),.src_in(good_frame_toggle),.dest_clk(ui_clk),.dest_out(good_frame_ui));
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(0),.INIT_SYNC_FF(1)) ecdc(
        .src_clk(1'b0),.src_in(bad_frame_toggle),.dest_clk(ui_clk),.dest_out(bad_frame_ui));
    wire armed=rx_frame||(pixel_valid&&sof);
    wire write=request&&armed&&rx_locked&&!overflow&&!flush_rx&&!wr_busy&&!fifo_reset;
    wire [35:0] din=frame_end?{1'b1,frame_good,2'b00,32'b0}:{2'b00,eol,sof,rgbx};
    // These registers drive FIFO write control. They must use the same
    // synchronous RX reset as the FIFO, never the asynchronous UI reset.
    always @(posedge rx_clk)begin
        if(fifo_reset)begin
            overflow<=0;rx_frame<=0;heartbeat_count<=0;
            good_frame_toggle<=0;bad_frame_toggle<=0;
        end
        else begin
            heartbeat_count<=heartbeat_count+1'b1;
            if(frame_end)begin
                if(frame_good)good_frame_toggle<=~good_frame_toggle;
                else bad_frame_toggle<=~bad_frame_toggle;
            end
            if(!rx_locked||flush_rx||wr_busy)rx_frame<=0;
            else if(pixel_valid&&sof&&!overflow)rx_frame<=1;
            else if(frame_end)rx_frame<=0;
            if(flush_rx)overflow<=0;
            else if(write&&full)begin overflow<=1;rx_frame<=0;end
        end
    end
    wire read=(flush||token_pop)&&!empty&&!rd_busy;
    xpm_fifo_async #(.FIFO_MEMORY_TYPE("block"),.FIFO_WRITE_DEPTH(DEPTH),
        .WRITE_DATA_WIDTH(36),.READ_DATA_WIDTH(36),.READ_MODE("fwft"),.FIFO_READ_LATENCY(0),
        .CDC_SYNC_STAGES(3),.USE_ADV_FEATURES("0000"),.DOUT_RESET_VALUE("0"),
        .ECC_MODE("no_ecc"),.SIM_ASSERT_CHK(1)) fifo(
        .rst(fifo_reset),.wr_clk(rx_clk),.rd_clk(ui_clk),.din(din),.wr_en(write&&!full),
        .full(full),.wr_rst_busy(wr_busy),.rd_en(read),.dout(fifo_out),.empty(empty),
        .rd_rst_busy(rd_busy),.sleep(1'b0),.injectsbiterr(1'b0),.injectdbiterr(1'b0),
        .almost_empty(),.almost_full(),.data_valid(),.dbiterr(),.sbiterr(),.overflow(),
        .prog_empty(),.prog_full(),.rd_data_count(),.underflow(),.wr_ack(),.wr_data_count());
    assign token=fifo_out;
    assign token_valid=!empty&&!rd_busy&&!flush&&source_present;
    assign flush_busy=flush||wr_busy_ui||rd_busy;
    reg last_heartbeat=0,seen_heartbeat=0;reg [31:0] age=0;reg [3:0] barrier=0;
    reg last_good_frame=0,last_bad_frame=0;
    reg [1:0] recovery_good_frames=0;
    wire new_good_frame=last_good_frame!=good_frame_ui;
    wire new_bad_frame=last_bad_frame!=bad_frame_ui;
    wire alive=locked_ui&&seen_heartbeat&&age<LOSS_CYCLES-1;
    always @(posedge ui_clk)begin
        discontinuity<=0;
        if(!resetn)begin
            flush<=1;source_present<=0;loss_events<=0;overflow_events<=0;
            age<=0;seen_heartbeat<=0;last_heartbeat<=0;barrier<=0;
            last_good_frame<=0;last_bad_frame<=0;recovery_good_frames<=0;
        end else begin
            last_heartbeat<=heartbeat_ui;
            last_good_frame<=good_frame_ui;last_bad_frame<=bad_frame_ui;
            if(last_heartbeat!=heartbeat_ui)begin age<=0;seen_heartbeat<=1;end
            else if(age<LOSS_CYCLES-1)age<=age+1'b1;
            source_present<=alive&&!flush;
            if(!flush&&((source_present&&!alive)||overflow_ui))begin
                flush<=1;barrier<=0;source_present<=0;discontinuity<=1;
                recovery_good_frames<=0;
                if(overflow_ui)overflow_events<=overflow_events+1'b1;
                if(source_present&&!alive)loss_events<=loss_events+1'b1;
            end
            if(flush)begin
                // RX must have seen the request before empty can authorize
                // release. Synchronizing flush_rx back supplies that proof.
                if(flush_seen&&empty&&!rd_busy&&!overflow_ui&&alive)begin
                    if(new_bad_frame)begin recovery_good_frames<=0;barrier<=0;end
                    else begin
                        if(new_good_frame&&recovery_good_frames<2)
                            recovery_good_frames<=recovery_good_frames+1'b1;
                        if(recovery_good_frames==2)begin
                            if(barrier==15)begin flush<=0;barrier<=0;end
                            else barrier<=barrier+1'b1;
                        end else barrier<=0;
                    end
                end else begin barrier<=0;recovery_good_frames<=0;end
            end
        end
    end
    xpm_cdc_single #(.DEST_SYNC_FF(3),.SRC_INPUT_REG(0),.INIT_SYNC_FF(1)) acdc(
        .src_clk(1'b0),.src_in(flush_rx),.dest_clk(ui_clk),.dest_out(flush_seen));
endmodule
