// An atomic UI snapshot crosses with XPM's complete four-phase handshake.
// Handshake state is not reset by MIG reset; stale source data is explicitly aged.
module bridge_telemetry #(parameter integer SAMPLE_CYCLES=1000000,STATUS_BITS=960)(
    input wire ui_clk,root_clk,input wire [STATUS_BITS-1:0] ui_status,
    input wire ref_locked,mig_resetn,ui_reset,calib,
    output wire [STATUS_BITS+191:0] payload
);
    reg [STATUS_BITS-1:0] source_hold=0;
    reg [31:0] sample_count=0;
    reg [1:0] hs_state=0;
    reg send=0;
    localparam integer CHUNKS=(STATUS_BITS+1023)/1024;
    localparam integer FIRST_BITS=STATUS_BITS<1024?STATUS_BITS:1024;
    wire received,dest_valid,dest_ack;
    wire [CHUNKS-1:0] chunk_received,chunk_valid;
    assign received=&chunk_received;
    wire [STATUS_BITS-1:0] dest_data;
    reg [STATUS_BITS-1:0] last_data=0;
    reg [63:0] root_ticks=0;
    reg [31:0] snapshots=0,age=32'hffffffff,reset_events=0;
    wire [3:0] flags;
    reg [1:0] ui_flags_registered=2'b01;
    always @(posedge ui_clk) ui_flags_registered<={calib,ui_reset};
    reg prior_reset=1,ever_ui_active=0,ui_restart_seen=0,ever_ref_locked=0,ref_loss_seen=0;
    xpm_cdc_array_single #(.DEST_SYNC_FF(3),.INIT_SYNC_FF(1),.SIM_ASSERT_CHK(1),.SRC_INPUT_REG(0),.WIDTH(4)) flags_cdc(
        .src_clk(1'b0),.src_in({ui_flags_registered,mig_resetn,ref_locked}),.dest_clk(root_clk),.dest_out(flags));
    // XPM handshake supports at most 1024 data bits. All chunks share the
    // held source snapshot; an external acknowledge waits for EVERY chunk.
    // Independent synchronizer arrival times cannot mix adjacent snapshots.
    xpm_cdc_handshake #(.DEST_EXT_HSK(CHUNKS>1),.DEST_SYNC_FF(3),.SRC_SYNC_FF(3),.INIT_SYNC_FF(1),.SIM_ASSERT_CHK(1),.WIDTH(FIRST_BITS)) snapshot_cdc(
        .src_clk(ui_clk),.src_in(source_hold[FIRST_BITS-1:0]),.src_send(send),.src_rcv(chunk_received[0]),
        .dest_clk(root_clk),.dest_out(dest_data[FIRST_BITS-1:0]),.dest_req(chunk_valid[0]),.dest_ack(dest_ack));
    generate for(genvar c=1;c<CHUNKS;c=c+1)begin:extra_chunks
        localparam integer BITS=STATUS_BITS-c*1024<1024?STATUS_BITS-c*1024:1024;
        xpm_cdc_handshake #(.DEST_EXT_HSK(1),.DEST_SYNC_FF(3),.SRC_SYNC_FF(3),.INIT_SYNC_FF(1),.SIM_ASSERT_CHK(1),.WIDTH(BITS)) snapshot_cdc(
            .src_clk(ui_clk),.src_in(source_hold[c*1024+:BITS]),.src_send(send),.src_rcv(chunk_received[c]),
            .dest_clk(root_clk),.dest_out(dest_data[c*1024+:BITS]),.dest_req(chunk_valid[c]),.dest_ack(dest_ack));
    end
    if(CHUNKS>1)begin:joined_snapshot
        reg [CHUNKS-1:0] seen=0;
        reg acknowledge=0,complete=0;
        assign dest_ack=acknowledge;
        assign dest_valid=complete;
        always @(posedge root_clk)begin
            complete<=0;
            if(!acknowledge)begin
                seen<=seen|chunk_valid;
                if(&(seen|chunk_valid))begin complete<=1;acknowledge<=1;seen<=0;end
            end else if(!(|chunk_valid))acknowledge<=0;
        end
    end else begin:single_snapshot
        assign dest_valid=chunk_valid[0];
        assign dest_ack=1'b0;
    end endgenerate
    always @(posedge ui_clk) begin
        case(hs_state)
            0: if (sample_count==SAMPLE_CYCLES-1) begin source_hold<=ui_status;sample_count<=0;hs_state<=1;end
               else sample_count<=sample_count+1'b1;
            1: begin send<=1;hs_state<=2;end
            2: if(received) begin send<=0;hs_state<=3;end
            3: if(!received) hs_state<=0;
        endcase
    end
    always @(posedge root_clk) begin
        root_ticks<=root_ticks+1'b1;
        if(dest_valid) begin last_data<=dest_data;snapshots<=snapshots+1'b1;age<=0;end
        else if(age!=32'hffffffff) age<=age+1'b1;
        prior_reset<=flags[2];
        if(!flags[2] && flags[1]) ever_ui_active<=1;
        if(flags[2] && !prior_reset) begin reset_events<=reset_events+1'b1;if(ever_ui_active) ui_restart_seen<=1;end
        if(flags[0]) ever_ref_locked<=1;
        if(ever_ref_locked && !flags[0]) ref_loss_seen<=1;
    end
    assign payload={24'b0,ref_loss_seen,ui_restart_seen,ever_ui_active,ever_ref_locked,flags,
                    reset_events,age,snapshots,root_ticks,last_data};
endmodule
