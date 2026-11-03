// Internal N3 stimulus; AXI-stream backpressure is supported. The later HDMI
// capture boundary needs a separate non-backpressurable input FIFO/drop policy.
// Memory bytes are R,G,B,X: numeric word = {X,B,G,R}.
module video_pattern_axis #(parameter integer WIDTH=1920,HEIGHT=1080)(
    input wire clk,resetn,start,input wire [15:0] frame,
    output wire busy,output reg done=0,output reg fault=0,
    output wire [31:0] tdata,output wire [3:0] tkeep,output wire tvalid,tuser,tlast,input wire tready
);
    reg active=0;reg [10:0] x=0,y=0;reg [15:0] identity=0;
    reg [1:0] valid=0,sof=0,eol=0,eof=0;
    wire advance=!valid[1]||tready;wire [23:0] colour;
    video_pattern_pixel pixel(.clk(clk),.ce(advance),.x(x),.y(y),.frame(identity),.rgb(colour));
    assign tdata={8'b0,colour[7:0],colour[15:8],colour[23:16]};
    assign tkeep=15;assign tvalid=valid[1];assign tuser=sof[1];assign tlast=eol[1];
    assign busy=active||(|valid);
    always @(posedge clk)begin
        done<=0;
        if(!resetn)begin active<=0;x<=0;y<=0;valid<=0;sof<=0;eol<=0;eof<=0;fault<=0;end
        else begin
            if(start)begin
                if(busy)fault<=1;
                else begin active<=1;x<=0;y<=0;identity<=frame;end
            end
            if(tvalid&&tready&&eof[1])done<=1;
            if(advance)begin
                valid<={valid[0],active};sof<={sof[0],active&&x==0&&y==0};
                eol<={eol[0],active&&x==WIDTH-1};eof<={eof[0],active&&x==WIDTH-1&&y==HEIGHT-1};
                if(active)begin
                    if(x==WIDTH-1)begin x<=0;if(y==HEIGHT-1)active<=0;else y<=y+1'b1;end
                    else x<=x+1'b1;
                end
            end
        end
    end
endmodule
