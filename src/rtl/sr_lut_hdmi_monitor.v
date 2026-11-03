`timescale 1ns/1ps

module sr_lut_hdmi_monitor (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 ACLK CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ACLK, ASSOCIATED_RESET ARESETN, FREQ_HZ 100000000" *)
    input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 ARESETN RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ARESETN, POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    input wire video_vsync_async,
    input wire video_locked,
    input wire video_underflow,
    input wire video_overflow,
    output reg [31:0] scan_frames,
    output wire [31:0] errors
);
    wire [3:0] synchronized_status;
    reg vsync_previous;
    reg underflow_previous, overflow_previous;
    reg [14:0] underflows, overflows;
    // Video-underflow and sync originate in the pixel domain. Independent
    // status bits cross via XPM, before fan-out into counters or AXI GPIO.
    xpm_cdc_array_single #(
        .DEST_SYNC_FF(3), .INIT_SYNC_FF(1), .SIM_ASSERT_CHK(0),
        .SRC_INPUT_REG(0), .WIDTH(4)
    ) status_cdc (
        .src_clk(1'b0), .dest_clk(aclk),
        .src_in({video_locked, video_overflow, video_underflow, video_vsync_async}),
        .dest_out(synchronized_status)
    );
    assign errors = {1'b0, synchronized_status[3], overflows, underflows};
    always @(posedge aclk) begin
        if (!aresetn) begin
            vsync_previous <= 0;
            scan_frames <= 0;
            underflow_previous <= 0;
            overflow_previous <= 0;
            underflows <= 0;
            overflows <= 0;
        end else begin
            vsync_previous <= synchronized_status[0];
            if (synchronized_status[0] && !vsync_previous) scan_frames <= scan_frames + 1;
            underflow_previous <= synchronized_status[1];
            overflow_previous <= synchronized_status[2];
            if (synchronized_status[1] && !underflow_previous && underflows != 15'h7fff)
                underflows <= underflows + 1;
            if (synchronized_status[2] && !overflow_previous && overflows != 15'h7fff)
                overflows <= overflows + 1;
        end
    end
endmodule
