`timescale 1ns/1ps
module tb_hdmi_output;
    reg clk = 0;
    always #5 clk = ~clk;
    reg resetn = 0;
    reg [31:0] input_data = 0;
    reg valid = 0, ready = 0, last = 0, user = 0;
    wire [23:0] output_data;
    wire output_valid, input_ready, output_last, output_user;
    reg vsync = 0, locked = 0, underflow = 0, overflow = 0;
    wire [31:0] frames, errors;
    sr_lut_rgbx_video_adapter adapter (
        .aclk(clk), .aresetn(resetn), .s_axis_tdata(input_data),
        .s_axis_tvalid(valid), .s_axis_tready(input_ready),
        .s_axis_tlast(last), .s_axis_tuser(user), .m_axis_tdata(output_data),
        .m_axis_tvalid(output_valid), .m_axis_tready(ready),
        .m_axis_tlast(output_last), .m_axis_tuser(output_user)
    );
    sr_lut_hdmi_monitor monitor (
        .aclk(clk), .aresetn(resetn), .video_vsync_async(vsync),
        .video_locked(locked), .video_underflow(underflow),
        .video_overflow(overflow), .scan_frames(frames), .errors(errors)
    );
    task color(input [31:0] source, input [23:0] expected);
        @(negedge clk);
        input_data = source;
        valid = 1; ready = 0; user = 1; last = 1;
        repeat (4) begin
            @(posedge clk); #1;
            if (output_data !== expected || !output_valid || input_ready || !output_user || !output_last)
                $fatal(1, "Stalled RGBX word or markers changed");
        end
        @(negedge clk); ready = 1;
        @(posedge clk); #1;
        if (!input_ready || !output_valid || output_data !== expected)
            $fatal(1, "Video transfer failed");
    endtask
    initial begin
        repeat (15) @(posedge clk);
        #1;
        if (output_valid || input_ready || frames != 0 || errors != 0)
            $fatal(1, "Reset did not suppress transfers and clear counters");
        @(negedge clk); resetn = 1;
        color(32'h00_00_00_ff, 24'hff0000);
        color(32'h00_00_ff_00, 24'h0000ff);
        color(32'h00_ff_00_00, 24'h00ff00);
        color(32'hff_56_34_12, 24'h125634);
        @(negedge clk); locked = 1; vsync = 1; underflow = 1;
        repeat (10) @(negedge clk);
        if (frames != 1 || errors[14:0] != 1 || !errors[30])
            $fatal(1, "A prolonged level must count as one event");
        vsync = 0; underflow = 0; overflow = 1;
        repeat (6) @(negedge clk);
        vsync = 1; underflow = 1;
        repeat (6) @(negedge clk);
        if (frames != 2 || errors[14:0] != 2 || errors[29:15] != 1)
            $fatal(1, "Frame/error counter mismatch");
        resetn = 0;
        repeat (3) @(negedge clk);
        if (frames != 0 || errors[29:0] != 0 || output_valid || input_ready)
            $fatal(1, "Restart did not clear state");
        $display("HDMI_OUTPUT_RTL_PASS colors=4 stall_cycles=16 monitor_events=5");
        $finish;
    end
    initial begin #10000; $fatal(1, "Test timeout"); end
endmodule
