// MM2S and DP use the same symbol clock. The FIFO bridges their burst schedules,
// not clock domains. It owns all prefetched pixels until released or drained.
module video_pair_fifo #(
    parameter integer WIDTH=1920,HEIGHT=1080,DEPTH=4096,PREFILL=1024,PIXELS_PER_BEAT=1
)(
    input wire clk,resetn,start,abort,release_frame,
    input wire [32*PIXELS_PER_BEAT-1:0] tdata,input wire [4*PIXELS_PER_BEAT-1:0] tkeep,
    input wire tvalid,tuser,tlast,output wire tready,
    output wire [47:0] pair_data,output wire pair_valid,input wire pair_pop,
    output wire prefilled,empty,reset_busy,
    output reg busy=0,received=0,retired=0,bad=0,
    output reg [31:0] pixels_received=0,pairs_consumed=0
);
    localparam COUNT_WIDTH=$clog2(DEPTH)+1;
    wire full,overflow,underflow,wr_busy,rd_busy;
    wire [COUNT_WIDTH-1:0] count;
    wire [63:0] dout;
    reg half=0,discard=0;reg [31:0] first=0;
    reg [11:0] x=0,y=0;
    assign reset_busy=wr_busy||rd_busy;
    assign tready=busy && !received && !reset_busy && (discard||!full);
    wire accepted=tvalid&&tready;
    wire wr_en=accepted&&(PIXELS_PER_BEAT==2||half)&&!discard&&!abort;
    wire [63:0] packed_data;
    generate if(PIXELS_PER_BEAT==2)assign packed_data=tdata;
    else assign packed_data={tdata,first};endgenerate
    initial if((PIXELS_PER_BEAT!=1&&PIXELS_PER_BEAT!=2)||WIDTH%2!=0)
        $error("Unsupported pixel packing");
    wire rd_en=!reset_busy&&!empty&&(discard||abort||pair_pop);
    assign pair_valid=busy&&!discard&&!abort&&!empty&&!reset_busy;
    // First stream word is in the low half of the stored pair; RGBX bytes are R,G,B,X.
    assign pair_data={dout[7:0],dout[15:8],dout[23:16],dout[39:32],dout[47:40],dout[55:48]};
    assign prefilled=busy&&!discard&&!bad&&count>=PREFILL;
    xpm_fifo_sync #(.FIFO_MEMORY_TYPE("block"),.FIFO_WRITE_DEPTH(DEPTH),
        .WRITE_DATA_WIDTH(64),.READ_DATA_WIDTH(64),.READ_MODE("fwft"),.FIFO_READ_LATENCY(0),
        .WR_DATA_COUNT_WIDTH(COUNT_WIDTH),.RD_DATA_COUNT_WIDTH(COUNT_WIDTH),
        .USE_ADV_FEATURES("0505"),.SIM_ASSERT_CHK(1),.DOUT_RESET_VALUE("0"),
        .PROG_FULL_THRESH(DEPTH-8),.PROG_EMPTY_THRESH(8)) storage(
        .wr_clk(clk),.rst(!resetn),.din(packed_data),.wr_en(wr_en),.rd_en(rd_en),
        .dout(dout),.full(full),.empty(empty),.overflow(overflow),.underflow(underflow),
        .wr_rst_busy(wr_busy),.rd_rst_busy(rd_busy),.wr_data_count(count),
        .sleep(1'b0),.injectsbiterr(1'b0),.injectdbiterr(1'b0));
    always @(posedge clk)begin
        retired<=0;
        if(!resetn)begin
            busy<=0;received<=0;bad<=0;half<=0;discard<=0;x<=0;y<=0;
            pixels_received<=0;pairs_consumed<=0;
        end else begin
            if(start)begin
                if(busy||!empty||reset_busy)bad<=1;
                else begin
                    busy<=1;received<=0;bad<=0;half<=0;discard<=0;x<=0;y<=0;
                    pixels_received<=0;pairs_consumed<=0;
                end
            end
            if(abort&&busy)begin discard<=1;bad<=1;half<=0;end
            if(accepted)begin
                if(tkeep!={4*PIXELS_PER_BEAT{1'b1}}||tuser!=(x==0&&y==0)||tlast!=(x==WIDTH-PIXELS_PER_BEAT))bad<=1;
                pixels_received<=pixels_received+PIXELS_PER_BEAT;
                if(PIXELS_PER_BEAT==1&&!discard&&!abort)begin half<=!half;if(!half)first<=tdata;end
                if(x==WIDTH-PIXELS_PER_BEAT)begin x<=0;if(y==HEIGHT-1)received<=1;else y<=y+1'b1;end
                else x<=x+PIXELS_PER_BEAT;
            end
            if(pair_pop&&!discard&&!abort)begin
                if(!pair_valid)bad<=1;else pairs_consumed<=pairs_consumed+1'b1;
            end
            if(overflow||underflow)bad<=1;
            if(release_frame)begin
                if(!busy||!received||!empty||half||pairs_consumed!=WIDTH*HEIGHT/2)bad<=1;
                else begin busy<=0;retired<=1;end
            end
            if(busy&&discard&&received&&empty&&!reset_busy)begin busy<=0;retired<=1;end
        end
    end
endmodule
