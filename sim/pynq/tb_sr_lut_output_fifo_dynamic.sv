`timescale 1ns/1ps

module tb_sr_lut_output_fifo_dynamic;
  localparam int SCALE = 4;
  localparam int BLOCK_BITS = SCALE * SCALE * 8;
  logic clk = 0, resetn = 0;
  logic [BLOCK_BITS-1:0] source_block;
  logic [1:0] source_channel;
  logic source_valid, source_ready, source_last;
  logic [9:0] active_width, active_height;
  logic frame_start;
  logic [23:0] output_data;
  logic output_valid, output_ready, output_last;
  integer errors = 0, checks = 0;

  sr_lut_output_fifo_dynamic #(
    .SCALE(SCALE), .MAX_FRAME_WIDTH(480), .MAX_FRAME_HEIGHT(320)
  ) dut (
    .clk_i(clk), .resetn_i(resetn), .s_block_i(source_block),
    .s_channel_i(source_channel), .s_valid_i(source_valid),
    .s_ready_o(source_ready), .s_last_i(source_last),
    .active_width_i(active_width), .active_height_i(active_height),
    .frame_start_i(frame_start), .m_axis_tdata_o(output_data),
    .m_axis_tvalid_o(output_valid), .m_axis_tready_i(output_ready),
    .m_axis_tlast_o(output_last)
  );

  always #5 clk = ~clk;

  function automatic logic [127:0] block_value(input integer block_index, input integer channel);
    integer element;
    begin
      block_value = '0;
      for (element = 0; element < 16; element = element + 1)
        block_value[element * 8 +: 8] = (block_index * 37 + channel * 71 + element) & 255;
    end
  endfunction

  function automatic logic [7:0] pixel_value(input integer block_index, input integer channel, input integer element);
    pixel_value = (block_index * 37 + channel * 71 + element) & 255;
  endfunction

  task automatic run_frame(input integer width, input integer height, input integer frame_id);
    integer input_index, output_index, cycles, block_index, channel;
    integer output_row, output_column, input_row, input_column, subrow, subcolumn, element;
    logic accepted_input, accepted_output, accepted_last;
    logic [23:0] accepted_data, expected_data;
    begin
      active_width = width; active_height = height; frame_start = 1'b1;
      @(posedge clk); #1; frame_start = 1'b0;
      input_index = 0; output_index = 0; cycles = 0;
      while (input_index < width * height * 3 || output_index < width * height * 16) begin
        @(negedge clk);
        output_ready = (cycles % 11 != 4 && cycles % 17 != 8);
        block_index = input_index / 3;
        channel = input_index % 3;
        source_block = block_value(block_index + frame_id * 64, channel);
        source_channel = channel;
        source_valid = (input_index < width * height * 3);
        source_last = (input_index == width * height * 3 - 1);
        #1;
        accepted_input = source_valid && source_ready;
        accepted_output = output_valid && output_ready;
        accepted_data = output_data;
        accepted_last = output_last;
        @(posedge clk); #1;
        if (accepted_input) input_index = input_index + 1;
        if (accepted_output) begin
          output_row = output_index / (width * 4);
          output_column = output_index % (width * 4);
          input_row = output_row / 4; input_column = output_column / 4;
          subrow = output_row % 4; subcolumn = output_column % 4;
          element = subrow * 4 + subcolumn;
          block_index = frame_id * 64 + input_row * width + input_column;
          expected_data = {
            pixel_value(block_index, 2, element),
            pixel_value(block_index, 1, element),
            pixel_value(block_index, 0, element)
          };
          if (accepted_data !== expected_data) begin
            errors = errors + 1;
            if (errors < 10) $display("dynamic output mismatch frame=%0d index=%0d actual=%06x expected=%06x", frame_id, output_index, accepted_data, expected_data);
          end
          if (accepted_last !== (output_index == width * height * 16 - 1)) errors = errors + 1;
          output_index = output_index + 1; checks = checks + 1;
        end
        cycles = cycles + 1;
        if (cycles > (width * height * 24 + 10000))
          $fatal(1, "dynamic output fifo timeout width=%0d height=%0d", width, height);
      end
      source_valid = 1'b0; output_ready = 1'b0;
    end
  endtask

  initial begin
    source_block = 0; source_channel = 0; source_valid = 0; source_last = 0;
    active_width = 0; active_height = 0; frame_start = 0; output_ready = 0;
    repeat (3) @(posedge clk); #1; resetn = 1;
    run_frame(320, 320, 1);
    run_frame(128, 128, 2);
    run_frame(320, 320, 3);
    run_frame(320, 180, 4);
    run_frame(319, 257, 5);
    run_frame(33, 47, 6);
    run_frame(240, 240, 7);
    run_frame(64, 320, 8);
    run_frame(32, 32, 9);
    run_frame(128, 320, 10);
    run_frame(180, 320, 11);
    run_frame(320, 128, 12);
    run_frame(320, 319, 13);
    run_frame(480, 270, 14);
    run_frame(480, 320, 15);
    if (errors != 0) $fatal(1, "DYNAMIC_OUTPUT_FIFO_FAIL errors=%0d", errors);
    $display("DYNAMIC_OUTPUT_FIFO_PASS frames=13 checks=%0d", checks);
    $finish;
  end
endmodule
