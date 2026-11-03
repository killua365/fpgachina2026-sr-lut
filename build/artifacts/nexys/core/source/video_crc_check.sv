// Checks readback against the CRC of the complete admitted HDMI frame. The
// descriptor is kept with its DDR buffer; repeated scans reuse the same CRC.
module video_crc_check #(
    parameter integer WIDTH=1920,HEIGHT=1080
)(
    input wire clk,resetn,start,input wire [31:0] expected_crc,
    input wire [63:0] tdata,input wire [7:0] tkeep,input wire tvalid,tuser,tlast,
    output wire tready,output reg busy=0,complete=0,bad=0,
    output reg [31:0] checked_pixels=0,observed_crc=0
);
    `include "frame_crc32.vh"
    reg [31:0] crc=32'hffffffff,x=0,next_crc;
    always @* begin
        next_crc=crc;
        for(integer lane=0;lane<8;lane=lane+1)next_crc=frame_crc_byte(next_crc,tdata[lane*8+:8]);
    end
    assign tready=busy&&!complete;
    always @(posedge clk)begin
        if(!resetn)begin busy<=0;complete<=0;bad<=0;checked_pixels<=0;observed_crc<=0;crc<=32'hffffffff;x<=0;end
        else if(start)begin
            busy<=1;complete<=0;bad<=0;checked_pixels<=0;observed_crc<=0;crc<=32'hffffffff;x<=0;
        end else if(tvalid&&tready)begin
            if(tkeep!=8'hff||tuser!=(checked_pixels==0)||tlast!=(x==WIDTH-2))bad<=1;
            crc<=next_crc;checked_pixels<=checked_pixels+2;
            x<=x==WIDTH-2?0:x+2;
            if(checked_pixels==WIDTH*HEIGHT-2)begin
                observed_crc<=~next_crc;complete<=1;busy<=0;
                if((~next_crc)!=expected_crc)bad<=1;
            end
        end
    end
endmodule
