// Reflected CRC-32/ISO-HDLC. Feed AXI bytes low-to-high, initialize to all ones,
// complement the final state. Polynomial and byte order match Python zlib.crc32.
function automatic [31:0] frame_crc_byte(input [31:0] state,input [7:0] octet);
    reg [31:0] c;
    begin
        c=state^{24'b0,octet};
        for(integer bitn=0;bitn<8;bitn=bitn+1)c=(c>>1)^(c[0]?32'hedb88320:32'b0);
        frame_crc_byte=c;
    end
endfunction
