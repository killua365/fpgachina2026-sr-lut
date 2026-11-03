`timescale 1ns/1ps
module tb_hdmi_vtc_reset #(parameter CHECK_SCANOUT = 0);
  reg axi_clk = 0, pixel_clk = 0;
  always #5 axi_clk = ~axi_clk;
  always #6.734 pixel_clk = ~pixel_clk;
  reg resetn = 0, aux_resetn = 0;
  wire video_resetn;
  reg [8:0] awaddr = 0;
  reg [31:0] wdata = 0;
  reg awvalid = 0, wvalid = 0;
  wire awready, wready, bvalid;
  wire [1:0] bresp;
  integer held_response;
  wire timing_de, timing_hs, timing_vs, timing_hb, timing_vb, gen_ce;
  wire display_de, display_vs, locked, underflow, overflow;
  wire [23:0] display_data;
  reg scan_enabled = 0;
  integer column = 0, row = 0;
  wire source_ready;

  generate if (CHECK_SCANOUT) begin : scanout
    sr_lut_s40_dyn_hdmi_video_out_0 video_out (
      .aclk(axi_clk), .aclken(1'b1), .aresetn(resetn),
      .s_axis_video_tdata(24'h125634), .s_axis_video_tvalid(scan_enabled),
      .s_axis_video_tready(source_ready), .s_axis_video_tuser(column == 0 && row == 0),
      .s_axis_video_tlast(column == 1279), .fid(1'b0),
      .vid_io_out_clk(pixel_clk), .vid_io_out_ce(1'b1), .vid_io_out_reset(~video_resetn),
      .vid_active_video(display_de), .vid_vsync(display_vs), .vid_data(display_data),
      .vtg_vsync(timing_vs), .vtg_hsync(timing_hs), .vtg_vblank(timing_vb),
      .vtg_hblank(timing_hb), .vtg_active_video(timing_de), .vtg_field_id(1'b0),
      .vtg_ce(gen_ce), .locked(locked), .overflow(overflow), .underflow(underflow));
    always @(posedge axi_clk) begin
      if (!resetn) begin column <= 0; row <= 0; end
      else if (scan_enabled && source_ready) begin
        if (column == 1279) begin
          column <= 0;
          row <= row == 719 ? 0 : row + 1;
        end else column <= column + 1;
      end
    end
  end else begin
    assign gen_ce = 0;
  end endgenerate

  sr_lut_s40_dyn_hdmi_reset_0 resets (
    .slowest_sync_clk(pixel_clk), .ext_reset_in(resetn),
    .aux_reset_in(aux_resetn), .mb_debug_sys_rst(1'b0),
    .dcm_locked(1'b1), .peripheral_aresetn(video_resetn));
  sr_lut_s40_dyn_hdmi_vtc_0 vtc (
    .clk(pixel_clk), .clken(1'b1), .gen_clken(gen_ce),
    .active_video_out(timing_de), .hsync_out(timing_hs), .vsync_out(timing_vs),
    .hblank_out(timing_hb), .vblank_out(timing_vb),
    .resetn(video_resetn), .s_axi_aclk(axi_clk),
    .s_axi_aclken(1'b1), .s_axi_aresetn(resetn),
    .sof_state(1'b0), .fsync_in(1'b0),
    .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
    .s_axi_wdata(wdata), .s_axi_wstrb(4'hf),
    .s_axi_wvalid(wvalid), .s_axi_wready(wready),
    .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(1'b1),
    .s_axi_araddr(9'b0), .s_axi_arvalid(1'b0), .s_axi_rready(1'b1));

  task automatic write_register(input [8:0] address, input [31:0] value,
                                output integer response);
    bit address_done, data_done;
    integer cycles;
    begin
      @(negedge axi_clk);
      awaddr = address; wdata = value; awvalid = 1; wvalid = 1;
      address_done = 0; data_done = 0; response = -1;
      for (cycles = 0; cycles < 2000; cycles = cycles + 1) begin
        @(posedge axi_clk);
        if (awvalid && awready) address_done = 1;
        if (wvalid && wready) data_done = 1;
        if (bvalid) begin response = bresp; break; end
        @(negedge axi_clk);
        if (address_done) awvalid = 0;
        if (data_done) wvalid = 0;
      end
      @(negedge axi_clk); awvalid = 0; wvalid = 0;
      $display("VTC_WRITE address=%h data=%h response=%0d video_resetn=%b",
               address, value, response, video_resetn);
    end
  endtask

  initial begin
    integer response;
    #1000; resetn = 1;
    #10000;
    if (video_resetn !== 0) $fatal(1, "Old auxiliary tie must hold native reset");
    write_register(0, 0, held_response);
    // Clear the incomplete transaction before exercising the corrected tie.
    resetn = 0; #1000; aux_resetn = 1; resetn = 1;
    #10000;
    if (video_resetn !== 1) $fatal(1, "Corrected auxiliary tie must release reset");
    write_register(0, 0, response);
    if (response != 0) $fatal(1, "VTC control write failed after reset release");
    write_register('h60, (720 << 16) | 1280, response);
    if (response != 0) $fatal(1, "VTC timing write failed after reset release");
    $display("HDMI_VTC_RESET_PASS old_write_response=%0d", held_response);
    if (CHECK_SCANOUT) begin
      write_register('h68, 0, response);
      write_register('h6c, 'h7f, response);
      write_register('h70, 1650, response);
      write_register('h74, (750 << 16) | 750, response);
      write_register('h78, (1430 << 16) | 1390, response);
      write_register('h7c, (1280 << 16) | 1280, response);
      write_register('h80, (729 << 16) | 724, response);
      write_register('h84, (1390 << 16) | 1390, response);
      write_register('h88, (1280 << 16) | 1280, response);
      write_register('h8c, (729 << 16) | 724, response);
      write_register('h90, (1390 << 16) | 1390, response);
      write_register('h94, 720 << 16, response);
      write_register(0, 'h03fdef07, response);
      scan_enabled = 1;
      wait (locked === 1);
      $display("VIDEO_LOCKED at %0t", $time);
      @(posedge display_vs);
      repeat (2) begin
        integer cycles, pixels;
        bit old_vs;
        cycles = 0; pixels = 0; old_vs = 1;
        forever begin
          @(negedge pixel_clk);
          if (display_vs && !old_vs) break;
          if (!locked || underflow || overflow) $fatal(1, "Unstable native scanout");
          if (display_de) begin
            pixels = pixels + 1;
            if (display_data !== 24'h125634) $fatal(1, "Native pixel mismatch");
          end
          old_vs = display_vs; cycles = cycles + 1;
        end
        $display("NATIVE_FRAME pixel_clocks=%0d active_pixels=%0d", cycles, pixels);
        if (cycles != 1650 * 750 || pixels != 1280 * 720)
          $fatal(1, "Native frame geometry does not match 720p60");
        @(negedge display_vs);
        @(posedge display_vs);
      end
      $display("HDMI_NATIVE_SCANOUT_PASS");
    end
    $finish;
  end
  initial begin #200000000; $fatal(1, "VTC reset/scanout regression timeout"); end
endmodule

module tb_hdmi_native_scanout;
  tb_hdmi_vtc_reset #(.CHECK_SCANOUT(1)) test();
endmodule
