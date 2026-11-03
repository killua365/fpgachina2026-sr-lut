// Pixel geometry and glyph lookup in two stages, RGB byte order R:G:B.
module video_pattern_pixel(input wire clk,ce,input wire [10:0] x,y,input wire [15:0] frame,
                           output reg [23:0] rgb=0);
    reg [35:0] info=0;
    function automatic [34:0] glyph(input [3:0] digit);
        begin case(digit)
            4'd0:glyph=35'b01110100011000110001100011000101110;
            4'd1:glyph=35'b00100011000010000100001000010001110;
            4'd2:glyph=35'b01110100010000100010001000100011111;
            4'd3:glyph=35'b11110000010000101110000010000111110;
            4'd4:glyph=35'b00010001100101010010111110001000010;
            4'd5:glyph=35'b11111100001000011110000010000111110;
            4'd6:glyph=35'b01110100001000011110100011000101110;
            4'd7:glyph=35'b11111000010001000100010000100001000;
            4'd8:glyph=35'b01110100011000101110100011000101110;
            4'd9:glyph=35'b01110100011000101111000010000101110;
            4'd10:glyph=35'b01110100011000111111100011000110001;
            4'd11:glyph=35'b11110100011000111110100011000111110;
            4'd12:glyph=35'b01111100001000010000100001000001111;
            4'd13:glyph=35'b11110100011000110001100011000111110;
            4'd14:glyph=35'b11111100001000011110100001000011111;
            4'd15:glyph=35'b11111100001000011110100001000010000;
        endcase end
    endfunction
    function automatic [35:0] prepare(input [10:0] px,py,input [15:0] f);
        reg [23:0] colour;reg border,letter;reg [3:0] digit;reg [5:0] bit_index;
        integer gx,gy,left,nibble,box;
        begin
            if(px<240)colour=24'hffffff;else if(px<480)colour=24'hffff00;
            else if(px<720)colour=24'h00ffff;else if(px<960)colour=24'h00ff00;
            else if(px<1200)colour=24'hff00ff;else if(px<1440)colour=24'hff0000;
            else if(px<1680)colour=24'h0000ff;else colour=0;
            if(py>=720)colour=(px[4]^py[4])?24'he0e0e0:24'h202020;
            if(py>=960)colour=px[0]?24'h0040ff:24'hff4000;
            box=f[7:0]*6+32;
            if(px>=box&&px<box+96&&py>=768&&py<832)colour=24'h00ffff;
            border=px==0||px==1919||py==0||py==1079;letter=0;digit=0;bit_index=0;
            if(px>=32&&px<128&&py>=32&&py<60)begin
                if(px<56)begin left=32;nibble=3;end else if(px<80)begin left=56;nibble=2;end
                else if(px<104)begin left=80;nibble=1;end else begin left=104;nibble=0;end
                gx=(px-left)/4;gy=(py-32)/4;colour=0;
                if(gx<5)begin letter=1;digit=(f>>(nibble*4))&15;bit_index=34-(gy*5+gx);end
            end
            prepare={border,letter,digit,bit_index,colour};
        end
    endfunction
    wire [34:0] bits=glyph(info[33:30]);
    always @(posedge clk)if(ce)begin
        info<=prepare(x,y,frame);
        if(info[35])rgb<=24'hffffff;
        else if(info[34]&&bits[info[29:24]])rgb<=24'h00ff00;
        else rgb<=info[23:0];
    end
endmodule
