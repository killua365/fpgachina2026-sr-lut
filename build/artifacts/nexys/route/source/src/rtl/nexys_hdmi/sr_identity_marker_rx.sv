// SRLT v1 reference receiver. Tap accepted RGBX pixels in ONE clock domain.
// Publish only with the enclosing complete/good frame; this is NOT a display
// counter. The integration must carry these fields with its frame descriptor
// and retire a new identity only after a complete DP scan.
module sr_identity_marker_rx(
    input wire clk, resetn,
    input wire pixel_valid, sof, eol,
    input wire [31:0] rgbx,
    input wire frame_end, frame_good,
    output reg valid=0, invalid=0, has_result=0,
    output reg [7:0] mode=0, scale=0,
    output reg [31:0] generation=0, source_sequence=0
);
    reg active=0, bad=0;
    reg [15:0] x=0;
    reg [1:0] y=0;
    reg [5:0] count=0;
    reg [7:0] row[0:19];
    reg [127:0] header=0;
    reg [31:0] checksum=32'hffffffff, received_crc=0;
    wire [7:0] value=rgbx[7:0];
    wire gray=(rgbx[7:0]==rgbx[15:8]) && (rgbx[7:0]==rgbx[23:16]);
    wire format_ok=(header[127:96]==32'h53524c54) && (header[95:88]==1) &&
        ((header[71:64]==1 && header[87:80]>=1 && header[87:80]<=3 &&
          (header[79:72]==2 || header[79:72]==4)) ||
         (header[71:64]==0 && header[87:80]==0 && header[79:72]==0 && header[31:0]==0));

    function automatic [31:0] crc_byte(input [31:0] previous, input [7:0] data);
        reg [31:0] c;
        integer i;
        begin
            c=previous ^ data;
            for(i=0;i<8;i=i+1)c=(c>>1) ^ (c[0] ? 32'hedb88320 : 32'b0);
            crc_byte=c;
        end
    endfunction

    always @(posedge clk)begin
        valid<=0;invalid<=0;
        if(!resetn)begin
            active<=0;bad<=0;x<=0;y<=0;count<=0;
            header<=0;checksum<=32'hffffffff;received_crc<=0;
            has_result<=0;mode<=0;scale<=0;generation<=0;source_sequence<=0;
        end else begin
            if(pixel_valid)begin
                if(sof)begin
                    // A new SOF never publishes a previous partial identity.
                    if(active)invalid<=1;
                    active<=1;bad<=!gray || eol;x<=1;y<=0;count<=1;
                    row[0]<=value;header<={120'b0,value};
                    checksum<=crc_byte(32'hffffffff,value);received_crc<=0;
                end else if(active)begin
                    if(y<2 && x<20)begin
                        count<=count+1'b1;
                        if(!gray)bad<=1;
                        if(y==0)begin
                            row[x]<=value;
                            if(x<16)begin
                                header<={header[119:0],value};
                                checksum<=crc_byte(checksum,value);
                            end else received_crc<={received_crc[23:0],value};
                        end else if(value!=(row[x]^8'hff))bad<=1;
                    end
                    if(eol)begin
                        if(y<2 && x<19)bad<=1;
                        x<=0;if(y<2)y<=y+1'b1;
                    end else if(x!=16'hffff)x<=x+1'b1;
                    else bad<=1;
                end
            end
            if(frame_end)begin
                active<=0;
                if(active && frame_good && !bad && count==40 && y==2 &&
                        received_crc==~checksum && format_ok && !pixel_valid)begin
                    valid<=1;has_result<=header[64];mode<=header[87:80];scale<=header[79:72];
                    generation<=header[63:32];source_sequence<=header[31:0];
                end else invalid<=1;
            end
        end
    end
endmodule
