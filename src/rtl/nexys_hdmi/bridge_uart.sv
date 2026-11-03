// SRB4: magic[4], version=1, payload_bytes=160, seq_u32le, payload[160],
// CRC16-CCITT u16le over the first 170 bytes (poly 1021, init FFFF).
module bridge_uart #(
    parameter integer CLOCK_HZ=100000000, BAUD=115200, INTERVAL=5000000, BUSINESS_IDENTITY=0
)(input wire clk,input wire [1279+1664*BUSINESS_IDENTITY:0] payload,output wire tx);
    localparam integer DIV=(CLOCK_HZ+BAUD/2)/BAUD;
    localparam integer PAYLOAD_BYTES=160+208*BUSINESS_IDENTITY,HEADER_BYTES=10+BUSINESS_IDENTITY;
    localparam integer CRC_OFFSET=HEADER_BYTES+PAYLOAD_BYTES,FRAME_BYTES=CRC_OFFSET+2;
    reg [PAYLOAD_BYTES*8-1:0] snapshot=0;
    reg [31:0] sequence_id=0,frame_sequence=0;
    reg [31:0] interval_count=0,baud_count=0;
    reg [8:0] byte_index=0;
    reg [3:0] bit_index=0;
    reg [9:0] shift=10'h3ff;
    reg [7:0] current_byte=0;
    reg [15:0] crc=16'hffff;
    reg active=0;
    wire [15:0] updated_crc;
    function automatic [15:0] crc_byte(input [15:0] old,input [7:0] data);
        reg [15:0] c;integer i;
        begin c=old^{data,8'b0};
            for(i=0;i<8;i=i+1) c=c[15] ? (c<<1)^16'h1021 : c<<1;
            crc_byte=c;
        end
    endfunction
    function automatic [7:0] frame_byte(input [8:0] index);
        begin
            if(index>=HEADER_BYTES)frame_byte=snapshot[(index-HEADER_BYTES)*8 +: 8];
            else if(index>=HEADER_BYTES-4)frame_byte=frame_sequence[(index-HEADER_BYTES+4)*8 +: 8];
            else case(index)
                0:frame_byte=8'h53;1:frame_byte=8'h52;2:frame_byte=8'h42;3:frame_byte=BUSINESS_IDENTITY?8'h35:8'h34;
                4:frame_byte=1;5:frame_byte=PAYLOAD_BYTES & 255;
                default:frame_byte=PAYLOAD_BYTES >> 8;
            endcase
        end
    endfunction
    assign updated_crc=crc_byte(crc,current_byte);
    assign tx=active ? shift[0] : 1'b1;
    initial if (DIV<2 || INTERVAL<=FRAME_BYTES*10*DIV) $fatal(1,"Invalid UART schedule");
    always @(posedge clk) begin
        if (interval_count<INTERVAL-1) interval_count<=interval_count+1;
        else if (!active) begin
            snapshot<=payload;frame_sequence<=sequence_id;sequence_id<=sequence_id+1;
            interval_count<=0;byte_index<=0;bit_index<=0;baud_count<=DIV-1;
            current_byte<=8'h53;shift<={1'b1,8'h53,1'b0};crc<=16'hffff;active<=1;
        end
        if (active) begin
            if (baud_count!=0) baud_count<=baud_count-1;
            else begin
                baud_count<=DIV-1;
                if (bit_index!=9) begin bit_index<=bit_index+1'b1;shift<={1'b1,shift[9:1]};end
                else begin
                    bit_index<=0;
                    if (byte_index==FRAME_BYTES-1) begin active<=0;shift<=10'h3ff;end
                    else begin
                        byte_index<=byte_index+1'b1;
                        if (byte_index<CRC_OFFSET) crc<=updated_crc;
                        if (byte_index==CRC_OFFSET-1) begin current_byte<=updated_crc[7:0];shift<={1'b1,updated_crc[7:0],1'b0};end
                        else if (byte_index==CRC_OFFSET) begin current_byte<=crc[15:8];shift<={1'b1,crc[15:8],1'b0};end
                        else begin current_byte<=frame_byte(byte_index+1'b1);shift<={1'b1,frame_byte(byte_index+1'b1),1'b0};end
                    end
                end
            end
        end
    end
endmodule
