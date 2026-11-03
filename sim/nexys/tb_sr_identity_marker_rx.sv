`timescale 1ns/1ps
module tb_sr_identity_marker_rx;
    reg clk=0;always #5 clk=~clk;
    reg resetn=0,pixel_valid=0,sof=0,eol=0,frame_end=0,frame_good=0;
    reg [31:0] rgbx=0;
    wire valid,invalid,has_result;
    wire [7:0] mode,scale;
    wire [31:0] generation,source_sequence;
    sr_identity_marker_rx dut(.*);
    // Independently serialized host encoder vectors, including IEEE CRC32.
    localparam [159:0] RESULT=160'h53524c5401020401123456789abcdef0996bf306;
    localparam [159:0] IDLE=160'h53524c540100000000000009000000001fff7745;
    integer checks=0;
    task automatic frame(input [159:0] packet,input integer corrupt,input integer lines,
                         input bit good,input bit expected,input bit result_present);
        reg [7:0] value;
        integer y,x;
        begin
            for(y=0;y<lines;y=y+1)begin
                for(x=0;x<24;x=x+1)begin
                    @(negedge clk);
                    pixel_valid=1;sof=(y==0 && x==0);eol=(x==23);
                    value=x<20 ? packet[159-x*8-:8] : 8'ha5;
                    if(y==1)value=value^8'hff;
                    if(corrupt==1 && y==1 && x==5)value=value^1;
                    rgbx={8'b0,value,value,value};
                    if(corrupt==2 && y==0 && x==7)rgbx[8]=~rgbx[8];
                    if(x%3==0)begin
                        @(negedge clk);pixel_valid=0;sof=0;eol=0;
                    end
                end
            end
            @(negedge clk);pixel_valid=0;sof=0;eol=0;frame_end=1;frame_good=good;
            @(negedge clk);
            if(valid!==expected || invalid!==!expected)$fatal(1,"valid/invalid mismatch check %0d",checks);
            if(expected)begin
                if(has_result!==result_present)$fatal(1,"result flag mismatch");
                if(result_present && (generation!==32'h12345678 || source_sequence!==32'h9abcdef0 || mode!==2 || scale!==4))
                    $fatal(1,"identity fields mismatch");
                if(!result_present && (generation!==9 || source_sequence!==0 || mode!==0 || scale!==0))
                    $fatal(1,"idle marker mismatch");
            end
            frame_end=0;frame_good=0;checks=checks+1;
            @(negedge clk);
            if(valid || invalid)$fatal(1,"completion must be one-cycle pulse");
        end
    endtask
    initial begin
        repeat(3)@(negedge clk);resetn=1;
        frame(RESULT,0,3,1,1,1);
        frame(RESULT,0,3,1,1,1); // repeated identity remains same, never invented sequence
        frame(RESULT,1,3,1,0,0);
        frame(RESULT,2,3,1,0,0);
        frame(RESULT^160'h1,0,3,1,0,0); // CRC corruption with valid row complement
        frame(RESULT,0,1,1,0,0);
        frame(RESULT,0,3,0,0,0); // enclosing frame damage rejects good marker
        frame(IDLE,0,3,1,1,0);
        frame(RESULT,0,3,1,1,1); // errors cannot poison next good frame
        $display("PASS sr_identity_marker_rx checks=%0d",checks);
        $finish;
    end
    initial begin #100000;$fatal(1,"simulation timeout");end
endmodule
