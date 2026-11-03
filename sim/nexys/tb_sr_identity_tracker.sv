`timescale 1ns/1ps
module tb_sr_identity_tracker;
    reg clk=0;always #5 clk=~clk;
    reg resetn=0,invalidate=0,claim_write=0,marker_valid=0,marker_invalid=0,publish=0,publish_good=0;
    reg claim_read=0,scan_complete=0,read_release=0;
    reg [1:0] write_index=0,read_index=0;
    reg [63:0] ticks=0,read_time=0;
    reg [80:0] marker_identity=0;
    wire [1663:0] status;
    sr_identity_tracker dut(.*);
    always @(posedge clk)if(resetn)ticks<=ticks+1;
    function automatic [31:0] word(input integer n);word=status[n*32+:32];endfunction
    task automatic step;begin @(negedge clk);end endtask
    task automatic put(input integer bank,input integer gen,input integer seq,input bit result_present,input bit valid_marker);
        begin
            step();claim_write=1;write_index=bank;
            step();claim_write=0;
            marker_identity={result_present,8'(result_present?2:0),8'(result_present?4:0),32'(gen),32'(seq)};
            marker_valid=valid_marker;marker_invalid=!valid_marker;
            step();marker_valid=0;marker_invalid=0;
            step();publish=1;publish_good=1;
            step();publish=0;publish_good=0;
        end
    endtask
    task automatic scan(input integer bank,input bit finish);
        begin
            step();claim_read=1;read_index=bank;read_time=ticks;
            step();claim_read=0;
            repeat(5)step();scan_complete=finish;
            step();scan_complete=0;
            step();read_release=1;
            step();read_release=0;
            step();
        end
    endtask
    initial begin
        repeat(3)step();resetn=1;
        put(0,7,1,1,1);
        if(word(7)!=0)$fatal(1,"Input or DDR completion counted as display");
        scan(0,1);scan(0,1);
        put(1,7,1,1,1);scan(1,1);
        if(word(7)!=1 || word(8)!=2)$fatal(1,"Same identity in new rasters/buffers counted twice");
        put(2,7,3,1,1);scan(2,1);
        if(word(7)!=2 || word(18)!=1)$fatal(1,"Sequence gap not recorded");
        scan(0,1);
        if(word(7)!=2 || word(9)!=1)$fatal(1,"Replayed old identity counted new");
        put(0,8,0,1,1);scan(0,1);
        put(1,9,0,0,1);scan(1,1);
        if(word(7)!=3 || word(12)!=1)$fatal(1,"Control frame counted new");
        put(2,9,1,1,0);scan(2,1);
        if(word(4)!=1 || word(10)!=1 || word(7)!=3)$fatal(1,"Bad marker credited");
        put(1,9,1,1,1);scan(1,0);
        if(word(11)!=1 || word(7)!=3)$fatal(1,"Incomplete scan credited");
        step();invalidate=1;step();invalidate=0;step();
        if(word(19)!=1)$fatal(1,"Undisplayed retired buffer not recorded");
        put(0,9,2,1,1);scan(0,1);
        put(1,9,3,1,1);scan(1,1);
        put(2,9,4,1,1);scan(2,1);
        if(word(7)!=6 || word(13)!=9 || word(14)!=4 || word(15)!=32'h402 || word(1)!=9)
            $fatal(1,"Final identity/status mismatch");
        // Journal retains events 3..6; each carries one clock-domain latency.
        for(integer k=0;k<4;k=k+1)begin
            if(word(20+k*8)<3 || word(20+k*8)>6 || word(25+k*8)!=0 || word(24+k*8)==0)
                $fatal(1,"Invalid event journal");
        end
        $display("PASS sr_identity_tracker: commit-vs-display, repeat, replay, gap, control, corrupt, incomplete, retirement, journal");
        $finish;
    end
    initial begin #100000;$fatal(1,"timeout");end
endmodule
