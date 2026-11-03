`timescale 1ns/1ps

module tb_sr_lut_axis_wrapper_dynamic #(parameter int SCALE = 4);
  localparam int W = 4, H = 3, INPUT_PIXELS = W * H, OUTPUT_PIXELS = INPUT_PIXELS * SCALE * SCALE;
  localparam logic [31:0] AXI_BASE = 32'h6000_0000;
  logic clk = 0, resetn = 0;
  logic [24:0] input_mem [0:INPUT_PIXELS-1];
  logic [24:0] output_mem [0:OUTPUT_PIXELS-1];
  logic [31:0] s_data, m_data;
  logic [3:0] s_keep, m_keep;
  logic s_valid, s_ready, s_last, m_valid, m_ready, m_last;
  logic [31:0] awaddr, wdata, araddr, rdata;
  logic [3:0] wstrb;
  logic awvalid, awready, wvalid, wready, bvalid, bready;
  logic arvalid, arready, rvalid, rready;
  logic [1:0] bresp, rresp;
  logic [31:0] perf_cycles, perf_in_bp, perf_out_bp, perf_starve;
  integer errors = 0;

  sr_lut_axis_wrapper_dynamic #(
    .SCALE(SCALE), .SAMPLE_BITS(SCALE == 2 ? 4 : 5),
    .Q(SCALE == 2 ? 16 : 32), .LEVELS(SCALE == 2 ? 17 : 9),
    .LUT_DEPTH(SCALE == 2 ? 83521 : 6561), .ADDR_BITS(SCALE == 2 ? 17 : 13),
    .SCHEDULER_CORES(SCALE == 2 ? 1 : 3),
    .MAX_FRAME_WIDTH(480), .MAX_FRAME_HEIGHT(320),
    .MIN_FRAME_WIDTH(2), .MIN_FRAME_HEIGHT(2),
    .MEM_FILE(SCALE == 2 ? "data/lut/bram_mem/model_s_x2_4bit.mem" : "data/lut/bram_mem/model_s_x4_5bit.mem")
  ) dut (
    .aclk(clk), .aresetn(resetn),
    .s_axis_tdata(s_data), .s_axis_tkeep(s_keep), .s_axis_tvalid(s_valid),
    .s_axis_tready(s_ready), .s_axis_tlast(s_last),
    .m_axis_tdata(m_data), .m_axis_tkeep(m_keep), .m_axis_tvalid(m_valid),
    .m_axis_tready(m_ready), .m_axis_tlast(m_last),
    .s_axi_ctrl_awaddr(awaddr), .s_axi_ctrl_awvalid(awvalid), .s_axi_ctrl_awready(awready),
    .s_axi_ctrl_wdata(wdata), .s_axi_ctrl_wstrb(wstrb), .s_axi_ctrl_wvalid(wvalid), .s_axi_ctrl_wready(wready),
    .s_axi_ctrl_bresp(bresp), .s_axi_ctrl_bvalid(bvalid), .s_axi_ctrl_bready(bready),
    .s_axi_ctrl_araddr(araddr), .s_axi_ctrl_arvalid(arvalid), .s_axi_ctrl_arready(arready),
    .s_axi_ctrl_rdata(rdata), .s_axi_ctrl_rresp(rresp), .s_axi_ctrl_rvalid(rvalid), .s_axi_ctrl_rready(rready),
    .perf_frame_cycles(perf_cycles), .perf_input_backpressure_cycles(perf_in_bp),
    .perf_output_backpressure_cycles(perf_out_bp), .perf_input_starvation_cycles(perf_starve)
  );

  always #5 clk = ~clk;

  task automatic axi_write(input logic [31:0] address, input logic [31:0] value);
    begin
      @(negedge clk); awaddr = AXI_BASE | address; wdata = value; wstrb = 4'hf;
      awvalid = 1; wvalid = 1; bready = 1;
      while (!(awready && wready)) @(posedge clk);
      @(negedge clk); awvalid = 0; wvalid = 0;
      while (!bvalid) @(posedge clk);
      @(negedge clk); bready = 0;
    end
  endtask

  task automatic axi_read(input logic [31:0] address, output logic [31:0] value);
    begin
      @(negedge clk); araddr = AXI_BASE | address; arvalid = 1; rready = 0;
      while (!arready) @(posedge clk);
      @(negedge clk); arvalid = 0; rready = 1;
      while (!rvalid) @(posedge clk);
      value = rdata;
      @(negedge clk); rready = 0;
    end
  endtask

  task automatic soft_reset;
    logic [31:0] status;
    begin
      axi_write(32'h00, 32'h2);
      repeat (3) @(posedge clk);
      axi_read(32'h0c, status);
      if (status[3:0] != 1) begin errors = errors + 1; $display("soft reset status=%08x", status); end
    end
  endtask

  task automatic check_reset_defaults;
    logic [31:0] width, height, status, input_words, output_words, error_code;
    begin
      axi_read(32'h04, width);
      axi_read(32'h08, height);
      axi_read(32'h0c, status);
      axi_read(32'h10, input_words);
      axi_read(32'h14, output_words);
      axi_read(32'h18, error_code);
      if (width != 320 || height != 180 || status[3:0] != 1
          || input_words != 57600 || output_words != 57600*SCALE*SCALE || error_code != 0) begin
        errors = errors + 1;
        $display("high-base reset readback width=%0d height=%0d status=%08x input=%0d output=%0d error=%08x",
                 width, height, status, input_words, output_words, error_code);
      end
    end
  endtask

  task automatic run_valid_frame;
    integer input_index, output_index, cycles;
    logic accepted_input, accepted_output, accepted_last;
    logic [31:0] accepted_data, status;
    begin
      axi_write(32'h04, W); axi_write(32'h08, H); axi_write(32'h00, 1);
      input_index = 0; output_index = 0; cycles = 0;
      s_valid = 0; s_last = 0; m_ready = 0;
      while (input_index < INPUT_PIXELS || output_index < OUTPUT_PIXELS) begin
        @(negedge clk); m_ready = (cycles % 7 != 0);
        if (s_valid && !s_ready) begin
          // Keep the current AXIS word stable while the line buffer is busy.
        end else if (input_index < INPUT_PIXELS) begin
          s_data = {8'b0, input_mem[input_index][23:0]}; s_keep = 4'hf;
          s_valid = 1; s_last = input_mem[input_index][24];
        end else begin s_valid = 0; s_last = 0; end
        #1;
        accepted_input = s_valid && s_ready;
        accepted_output = m_valid && m_ready;
        accepted_data = m_data; accepted_last = m_last;
        @(posedge clk); #1;
        if (accepted_input) input_index = input_index + 1;
        if (accepted_output) begin
          if (accepted_data !== {8'b0, output_mem[output_index][23:0]}) errors = errors + 1;
          if (accepted_last !== output_mem[output_index][24]) errors = errors + 1;
          output_index = output_index + 1;
        end
        cycles = cycles + 1;
        if (cycles > 200000) $fatal(1, "dynamic wrapper valid frame timeout");
      end
      s_valid = 0; m_ready = 0;
      axi_read(32'h0c, status);
      if (!status[2] || status[3]) begin errors = errors + 1; $display("valid frame status=%08x", status); end
    end
  endtask

  task automatic check_max_word_counts;
    logic [31:0] input_words, output_words;
    begin
      soft_reset();
      axi_write(32'h04, 480);
      axi_write(32'h08, 320);
      axi_write(32'h00, 1);
      axi_read(32'h10, input_words);
      axi_read(32'h14, output_words);
      if (input_words != 153600 || output_words != 153600*SCALE*SCALE) begin
        errors = errors + 1;
        $display(
          "max-size word count mismatch input=%0d output=%0d",
          input_words,
          output_words
        );
      end
      soft_reset();
    end
  endtask

  initial begin
    if (SCALE == 2) begin
      $readmemh("data/golden/image_stream/x2_4bit/small_input_axis.mem", input_mem);
      $readmemh("data/golden/image_stream/x2_4bit/small_output_axis.mem", output_mem);
    end else begin
      $readmemh("data/golden/image_stream/x4_5bit/small_input_axis.mem", input_mem);
      $readmemh("data/golden/image_stream/x4_5bit/small_output_axis.mem", output_mem);
    end
    s_data=0; s_keep=4'hf; s_valid=0; s_last=0; m_ready=0;
    awaddr=0; wdata=0; wstrb=0; awvalid=0; wvalid=0; bready=0;
    araddr=0; arvalid=0; rready=0;
    repeat (3) @(posedge clk); #1; resetn = 1;
    check_reset_defaults();
    check_max_word_counts();
    run_valid_frame();
    run_valid_frame();
    soft_reset();
    axi_write(32'h04, 1); axi_write(32'h08, H); axi_write(32'h00, 1);
    begin : invalid_check
      logic [31:0] status, error_code;
      axi_read(32'h0c, status); axi_read(32'h18, error_code);
      if (!status[3] || !(error_code & 1)) errors = errors + 1;
    end
    soft_reset();
    axi_write(32'h04, W); axi_write(32'h08, H); axi_write(32'h00, 1);
    axi_write(32'h04, W);
    begin : busy_check
      logic [31:0] status, error_code;
      axi_read(32'h0c, status); axi_read(32'h18, error_code);
      if (!status[3] || !(error_code & 32'h20)) errors = errors + 1;
    end
    soft_reset();
    axi_write(32'h04, W); axi_write(32'h08, H); axi_write(32'h00, 1);
    @(negedge clk); s_data = {8'b0, input_mem[0][23:0]}; s_keep=4'hf; s_valid=1; s_last=1;
    while (!s_ready) @(posedge clk);
    @(posedge clk); #1; s_valid=0; s_last=0;
    repeat (3) @(posedge clk);
    begin : tlast_check
      logic [31:0] status, error_code;
      axi_read(32'h0c, status); axi_read(32'h18, error_code);
      if (!status[3] || !(error_code & 32'h40)) errors = errors + 1;
    end
    if (errors != 0) $fatal(1, "DYNAMIC_AXIS_WRAPPER_FAIL errors=%0d", errors);
    $display("DYNAMIC_AXIS_WRAPPER_PASS scale=%0d frames=2 backpressure=true", SCALE);
    $finish;
  end
endmodule

module tb_sr_lut_axis_wrapper_dynamic_x2;
  tb_sr_lut_axis_wrapper_dynamic #(.SCALE(2)) suite();
endmodule
