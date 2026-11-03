`timescale 1ns/1ps
module tb_sr_identity_telemetry;
    reg ui_clk=0,root_clk=0,pause_ui=0;
    always #5 if(!pause_ui)ui_clk=~ui_clk;
    always #4.31 root_clk=~root_clk;
    reg [31:0] stamp=1;
    always @(posedge ui_clk)stamp<=stamp+1;
    wire [2623:0] ui_status;
    for(genvar w=0;w<82;w=w+1)begin
        assign ui_status[w*32+:32]=stamp^(32'h1020304*w);
    end
    wire [2815:0] payload;
    reg ref_locked=1,mig_resetn=1,ui_reset=0,calib=1;
    bridge_telemetry #(.STATUS_BITS(2624),.SAMPLE_CYCLES(19)) dut(.*);
    wire [1151:0] old_payload;
    bridge_telemetry #(.SAMPLE_CYCLES(19)) legacy(.ui_clk(ui_clk),.root_clk(root_clk),
        .ui_status(ui_status[959:0]),.ref_locked(ref_locked),.mig_resetn(mig_resetn),
        .ui_reset(ui_reset),.calib(calib),.payload(old_payload));
    integer checked=0,old_checked=0;
    reg [31:0] previous=0,old_previous=0;
    always @(negedge root_clk)begin
        if(payload[2688+:32]!=previous)begin
            if(payload[2688+:32]!=previous+1)$fatal(1,"Snapshot sequence jumped");
            previous=payload[2688+:32];checked=checked+1;
            for(integer j=0;j<82;j=j+1)
                if(payload[j*32+:32] !== (payload[31:0]^(32'h1020304*j)))
                    $fatal(1,"Mixed words across three asynchronous snapshot chunks");
        end
        if(old_payload[1024+:32]!=old_previous)begin
            old_previous=old_payload[1024+:32];old_checked=old_checked+1;
            for(integer j=0;j<30;j=j+1)
                if(old_payload[j*32+:32] !== (old_payload[31:0]^(32'h1020304*j)))
                    $fatal(1,"Legacy snapshot changed");
        end
    end
    initial begin
        wait(checked==5);
        // Delay one destination chunk: the other two may complete, but the
        // combined record and source acknowledgement must remain unchanged.
        force dut.chunk_valid[2]=1'b0;
        repeat(100)@(negedge root_clk);
        if(checked!=5)$fatal(1,"Partial chunk set published");
        release dut.chunk_valid[2];
        wait(checked==10);
        @(negedge ui_clk);pause_ui=1;
        repeat(100)@(negedge root_clk);
        begin integer saved_count;saved_count=checked;
            repeat(100)@(negedge root_clk);
            if(checked!=saved_count || payload[2720+:32]<100)$fatal(1,"Stopped UI clock was not aged");
        end
        pause_ui=0;
        wait(checked>=20);
        if(old_checked<10)$fatal(1,"Legacy monitor stalled");
        $display("PASS: atomic 2624-bit snapshot, delayed chunk, stopped clock, legacy snapshot");$finish;
    end
    initial begin #100000;$fatal(1,"Telemetry timeout");end
endmodule

