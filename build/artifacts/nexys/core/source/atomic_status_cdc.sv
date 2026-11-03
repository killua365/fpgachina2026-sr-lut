// Read-only coherent snapshots. An absent source clock leaves the last snapshot;
// consumers must use their independent source-present watchdog, never freshness
// inferred from unchanged counters.
module atomic_status_cdc #(parameter integer WIDTH=192)(
    input wire src_clk,dest_clk,src_resetn,dest_resetn,
    input wire [WIDTH-1:0] status,output reg [WIDTH-1:0] snapshot=0
);
    reg [WIDTH-1:0] held=0;reg send=0;reg [1:0] state=0;
    wire ack,valid;wire [WIDTH-1:0] received;
    xpm_cdc_handshake #(.WIDTH(WIDTH),.DEST_EXT_HSK(0),.DEST_SYNC_FF(3),.SRC_SYNC_FF(3),
        .INIT_SYNC_FF(1),.SIM_ASSERT_CHK(1)) bridge(
        .src_clk(src_clk),.src_in(held),.src_send(send),.src_rcv(ack),
        .dest_clk(dest_clk),.dest_req(valid),.dest_out(received),.dest_ack(1'b0));
    always @(posedge src_clk)begin
        if(!src_resetn)begin send<=0;state<=0;held<=0;end
        else case(state)
            0:begin held<=status;state<=1;end
            1:begin send<=1;state<=2;end
            2:if(ack)begin send<=0;state<=3;end
            3:if(!ack)state<=0;
        endcase
    end
    always @(posedge dest_clk)begin
        if(!dest_resetn)snapshot<=0;
        else if(valid)snapshot<=received;
    end
endmodule
