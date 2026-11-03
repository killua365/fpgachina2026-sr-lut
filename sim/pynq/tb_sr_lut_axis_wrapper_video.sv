`timescale 1ns/1ps

module tb_sr_lut_axis_wrapper_video;
  localparam int W = 4;
  localparam int H = 3;
  localparam int INPUT_PIXELS = W * H;
  localparam int OW = W * 4;
  localparam int OH = H * 4;
  localparam int OUTPUT_PIXELS = OW * OH;
  localparam int VIDEO_WORDS = INPUT_PIXELS * 6;
  localparam logic [31:0] AXI_BASE = 32'h6000_0000;

  logic clk = 0;
  logic resetn = 0;
  logic [24:0] input_mem [0:INPUT_PIXELS-1];
  logic [24:0] output_mem [0:OUTPUT_PIXELS-1];
  logic [31:0] video_words [0:VIDEO_WORDS-1];
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

  sr_lut_axis_wrapper_video #(
    .MAX_FRAME_WIDTH(480), .MAX_FRAME_HEIGHT(320),
    .MIN_FRAME_WIDTH(2), .MIN_FRAME_HEIGHT(2),
    .MEM_FILE("data/lut/bram_mem/model_s_x4_5bit.mem")
  ) dut (
    .aclk(clk), .aresetn(resetn),
    .s_axis_tdata(s_data), .s_axis_tkeep(s_keep),
    .s_axis_tvalid(s_valid), .s_axis_tready(s_ready),
    .s_axis_tlast(s_last), .m_axis_tdata(m_data),
    .m_axis_tkeep(m_keep), .m_axis_tvalid(m_valid),
    .m_axis_tready(m_ready), .m_axis_tlast(m_last),
    .s_axi_ctrl_awaddr(awaddr), .s_axi_ctrl_awvalid(awvalid),
    .s_axi_ctrl_awready(awready), .s_axi_ctrl_wdata(wdata),
    .s_axi_ctrl_wstrb(wstrb), .s_axi_ctrl_wvalid(wvalid),
    .s_axi_ctrl_wready(wready), .s_axi_ctrl_bresp(bresp),
    .s_axi_ctrl_bvalid(bvalid), .s_axi_ctrl_bready(bready),
    .s_axi_ctrl_araddr(araddr), .s_axi_ctrl_arvalid(arvalid),
    .s_axi_ctrl_arready(arready), .s_axi_ctrl_rdata(rdata),
    .s_axi_ctrl_rresp(rresp), .s_axi_ctrl_rvalid(rvalid),
    .s_axi_ctrl_rready(rready), .perf_frame_cycles(perf_cycles),
    .perf_input_backpressure_cycles(perf_in_bp),
    .perf_output_backpressure_cycles(perf_out_bp),
    .perf_input_starvation_cycles(perf_starve)
  );

  always #5 clk = ~clk;

  function automatic logic [7:0] clip(input integer value);
    if (value < 0) clip = 0;
    else if (value > 255) clip = 255;
    else clip = value[7:0];
  endfunction

  function automatic logic [7:0] calc_y(input logic [23:0] rgb);
    integer value;
    begin
      value = 66 * rgb[7:0] + 129 * rgb[15:8]
          + 25 * rgb[23:16] + 128;
      calc_y = clip((value >>> 8) + 16);
    end
  endfunction

  function automatic logic [7:0] calc_u(input logic [23:0] rgb);
    integer value;
    begin
      value = -38 * rgb[7:0] - 74 * rgb[15:8]
          + 112 * rgb[23:16] + 128;
      calc_u = clip((value >>> 8) + 128);
    end
  endfunction

  function automatic logic [7:0] calc_v(input logic [23:0] rgb);
    integer value;
    begin
      value = 112 * rgb[7:0] - 94 * rgb[15:8]
          - 18 * rgb[23:16] + 128;
      calc_v = clip((value >>> 8) + 128);
    end
  endfunction

  function automatic logic [31:0] crc_byte(
    input logic [31:0] crc,
    input logic [7:0] data
  );
    logic [31:0] value;
    integer bit_index;
    begin
      value = crc ^ data;
      for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
        value = value[0] ? (value >> 1) ^ 32'hedb88320 : value >> 1;
      crc_byte = value;
    end
  endfunction

  function automatic logic [31:0] crc_word(
    input logic [31:0] crc,
    input logic [31:0] data
  );
    logic [31:0] value;
    begin
      value = crc_byte(crc, data[7:0]);
      value = crc_byte(value, data[15:8]);
      value = crc_byte(value, data[23:16]);
      crc_word = crc_byte(value, data[31:24]);
    end
  endfunction

  task automatic axi_write(
    input logic [31:0] address,
    input logic [31:0] value
  );
    begin
      @(negedge clk);
      awaddr = AXI_BASE | address;
      wdata = value;
      wstrb = 4'hf;
      awvalid = 1;
      wvalid = 1;
      bready = 1;
      while (!(awready && wready)) @(posedge clk);
      @(negedge clk);
      awvalid = 0;
      wvalid = 0;
      while (!bvalid) @(posedge clk);
      @(negedge clk);
      bready = 0;
    end
  endtask

  task automatic axi_read(
    input logic [31:0] address,
    output logic [31:0] value
  );
    begin
      @(negedge clk);
      araddr = AXI_BASE | address;
      arvalid = 1;
      rready = 0;
      while (!arready) @(posedge clk);
      @(negedge clk);
      arvalid = 0;
      rready = 1;
      while (!rvalid) @(posedge clk);
      value = rdata;
      @(negedge clk);
      rready = 0;
    end
  endtask

  task automatic soft_reset;
    begin
      axi_write(32'h00, 32'h2);
      repeat (3) @(posedge clk);
    end
  endtask

  task automatic build_video_words;
    integer row, column, word_index;
    integer top0, top1, top2, top3, bottom0, bottom1, bottom2, bottom3;
    begin
      word_index = 0;
      for (row = 0; row < OH; row = row + 2) begin
        for (column = 0; column < OW; column = column + 4) begin
          top0 = row * OW + column;
          top1 = top0 + 1;
          top2 = top0 + 2;
          top3 = top0 + 3;
          bottom0 = top0 + OW;
          bottom1 = bottom0 + 1;
          bottom2 = bottom0 + 2;
          bottom3 = bottom0 + 3;
          video_words[word_index] = {
            calc_y(output_mem[bottom1][23:0]),
            calc_y(output_mem[bottom0][23:0]),
            calc_y(output_mem[top1][23:0]),
            calc_y(output_mem[top0][23:0])
          };
          video_words[word_index + 1] = {
            calc_y(output_mem[top3][23:0]),
            calc_y(output_mem[top2][23:0]),
            calc_v(output_mem[top0][23:0]),
            calc_u(output_mem[top0][23:0])
          };
          video_words[word_index + 2] = {
            calc_v(output_mem[top2][23:0]),
            calc_u(output_mem[top2][23:0]),
            calc_y(output_mem[bottom3][23:0]),
            calc_y(output_mem[bottom2][23:0])
          };
          word_index = word_index + 3;
        end
      end
      if (word_index != VIDEO_WORDS) $fatal(1, "video vector size mismatch");
    end
  endtask

  task automatic run_frame(input logic video_mode);
    integer input_index, output_index, expected_words, cycles;
    logic [31:0] accepted_data, expected_data, word_count, status;
    logic [31:0] expected_crc, actual_crc;
    logic accepted_input, accepted_output, accepted_last;
    begin
      axi_write(32'h04, W);
      axi_write(32'h08, H);
      axi_write(32'h1c, video_mode);
      axi_write(32'h00, 1);
      expected_words = video_mode ? VIDEO_WORDS : OUTPUT_PIXELS;
      axi_read(32'h14, word_count);
      if (word_count != expected_words) begin
        errors = errors + 1;
        $display("mode=%0d output word count=%0d expected=%0d",
                 video_mode, word_count, expected_words);
      end

      input_index = 0;
      output_index = 0;
      cycles = 0;
      expected_crc = 32'hffffffff;
      s_valid = 0;
      s_last = 0;
      m_ready = 0;
      while (input_index < INPUT_PIXELS || output_index < expected_words) begin
        @(negedge clk);
        m_ready = ((cycles % 7) != 2);
        if (!s_valid || s_ready) begin
          if (input_index < INPUT_PIXELS) begin
            s_data = {8'b0, input_mem[input_index][23:0]};
            s_keep = 4'hf;
            s_valid = 1;
            s_last = input_mem[input_index][24];
          end else begin
            s_valid = 0;
            s_last = 0;
          end
        end
        #1;
        accepted_input = s_valid && s_ready;
        accepted_output = m_valid && m_ready;
        accepted_data = m_data;
        accepted_last = m_last;
        @(posedge clk);
        #1;
        if (accepted_input) input_index = input_index + 1;
        if (accepted_output) begin
          expected_data = video_mode ? video_words[output_index]
              : {8'b0, output_mem[output_index][23:0]};
          if (accepted_data !== expected_data) begin
            errors = errors + 1;
            $display("mode=%0d word=%0d got=%08x expected=%08x",
                     video_mode, output_index, accepted_data, expected_data);
          end
          if (m_keep !== 4'hf) errors = errors + 1;
          if (accepted_last !== (output_index == expected_words - 1)) begin
            errors = errors + 1;
            $display("mode=%0d TLAST mismatch word=%0d", video_mode, output_index);
          end
          expected_crc = crc_word(expected_crc, accepted_data);
          output_index = output_index + 1;
        end
        cycles = cycles + 1;
        if (cycles > 300000) $fatal(1, "video wrapper timeout mode=%0d", video_mode);
      end
      s_valid = 0;
      m_ready = 0;
      axi_read(32'h0c, status);
      axi_read(32'h30, actual_crc);
      if (!status[2] || status[3]) begin
        errors = errors + 1;
        $display("mode=%0d completion status=%08x", video_mode, status);
      end
      if (actual_crc !== ~expected_crc) begin
        errors = errors + 1;
        $display("mode=%0d CRC=%08x expected=%08x",
                 video_mode, actual_crc, ~expected_crc);
      end
      if (actual_crc !== (video_mode ? 32'h248b8620 : 32'h9cc1d85c)) begin
        errors = errors + 1;
        $display("mode=%0d CRC does not match external zlib vector: %08x",
                 video_mode, actual_crc);
      end
      soft_reset();
    end
  endtask

  initial begin
    logic [31:0] mode, status, error_code;
    $readmemh("data/golden/image_stream/x4_5bit/small_input_axis.mem",
              input_mem);
    $readmemh("data/golden/image_stream/x4_5bit/small_output_axis.mem",
              output_mem);
    build_video_words();
    s_data = 0;
    s_keep = 4'hf;
    s_valid = 0;
    s_last = 0;
    m_ready = 0;
    awaddr = 0;
    wdata = 0;
    wstrb = 0;
    awvalid = 0;
    wvalid = 0;
    bready = 0;
    araddr = 0;
    arvalid = 0;
    rready = 0;
    repeat (3) @(posedge clk);
    #1;
    resetn = 1;

    axi_read(32'h1c, mode);
    if (mode != 0) errors = errors + 1;
    run_frame(1'b0);
    run_frame(1'b1);

    axi_write(32'h1c, 2);
    axi_write(32'h00, 1);
    axi_read(32'h0c, status);
    axi_read(32'h18, error_code);
    if (!status[3] || !(error_code & 32'h200)) begin
      errors = errors + 1;
      $display("invalid mode was not rejected status=%08x error=%08x",
               status, error_code);
    end

    if (errors != 0) $fatal(1, "VIDEO_AXIS_WRAPPER_FAIL errors=%0d", errors);
    $display("VIDEO_AXIS_WRAPPER_PASS rgbx_words=%0d mb420_words=%0d",
             OUTPUT_PIXELS, VIDEO_WORDS);
    $finish;
  end
endmodule
