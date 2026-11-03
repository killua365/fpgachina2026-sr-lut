`timescale 1ns/1ps

module tb_sr_lut_video420_packer;
  localparam int WIDTH = 8;
  localparam int HEIGHT = 2;
  localparam int PIXELS = WIDTH * HEIGHT;
  localparam int WORDS = PIXELS * 3 / 8;

  logic clk = 0;
  logic resetn = 0;
  logic frame_start = 0;
  logic [23:0] s_rgb = 0;
  logic s_valid = 0;
  logic s_ready;
  logic s_last = 0;
  logic [31:0] m_data;
  logic [3:0] m_keep;
  logic m_valid;
  logic m_ready = 0;
  logic m_last;
  logic [31:0] expected [0:WORDS-1];
  integer input_index = 0;
  integer output_index = 0;
  integer errors = 0;
  integer cycles = 0;

  sr_lut_video420_packer #(.MAX_OUTPUT_WIDTH(1280)) dut (
    .clk_i(clk), .resetn_i(resetn), .frame_start_i(frame_start),
    .output_width_i(WIDTH), .s_rgb_i(s_rgb), .s_valid_i(s_valid),
    .s_ready_o(s_ready), .s_last_i(s_last), .m_data_o(m_data),
    .m_keep_o(m_keep), .m_valid_o(m_valid), .m_ready_i(m_ready),
    .m_last_o(m_last)
  );

  always #5 clk = ~clk;

  initial begin
    expected[0] = 32'h5d562620;
    expected[1] = 32'h342d7b86;
    expected[2] = 32'h7f836b64;
    expected[3] = 32'h7972423b;
    expected[4] = 32'h50498280;
    expected[5] = 32'h867d8680;

    repeat (3) @(posedge clk);
    resetn = 1;
    @(negedge clk);
    frame_start = 1;
    @(negedge clk);
    frame_start = 0;

    while (output_index < WORDS) begin
      @(negedge clk);
      // Deterministic stalls exercise stability and simultaneous packet reload.
      m_ready = ((cycles % 5) != 1);
      if (!s_valid || s_ready) begin
        if (input_index < PIXELS) begin
          s_rgb[7:0] = 10 + input_index * 11;
          s_rgb[15:8] = 20 + input_index * 7;
          s_rgb[23:16] = 30 + input_index * 5;
          s_valid = 1;
          s_last = (input_index == PIXELS - 1);
        end else begin
          s_valid = 0;
          s_last = 0;
        end
      end
      #1;
      begin : sample_handshake
        logic accepted_input;
        logic accepted_output;
        logic [31:0] accepted_data;
        logic accepted_last;
        accepted_input = s_valid && s_ready;
        accepted_output = m_valid && m_ready;
        accepted_data = m_data;
        accepted_last = m_last;
        @(posedge clk);
        #1;
        if (accepted_input) input_index = input_index + 1;
        if (accepted_output) begin
          if (accepted_data !== expected[output_index]) begin
            errors = errors + 1;
            $display("word %0d mismatch got=%08x expected=%08x",
                     output_index, accepted_data, expected[output_index]);
          end
          if (m_keep !== 4'hf) begin
            errors = errors + 1;
            $display("TKEEP mismatch word=%0d keep=%x", output_index, m_keep);
          end
          if (accepted_last !== (output_index == WORDS - 1)) begin
            errors = errors + 1;
            $display("TLAST mismatch word=%0d last=%0d", output_index, accepted_last);
          end
          output_index = output_index + 1;
        end
      end
      cycles = cycles + 1;
      if (cycles > 500) $fatal(1, "video420 packer timeout");
    end

    s_valid = 0;
    m_ready = 0;
    if (input_index != PIXELS) begin
      errors = errors + 1;
      $display("input count mismatch %0d/%0d", input_index, PIXELS);
    end
    if (errors != 0) $fatal(1, "VIDEO420_PACKER_FAIL errors=%0d", errors);
    $display("VIDEO420_PACKER_PASS words=%0d", output_index);
    $finish;
  end
endmodule
