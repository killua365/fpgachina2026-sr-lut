`timescale 1ns/1ps

module tb_sr_lut_line_buffer_dynamic;
  logic clk = 0;
  logic resetn = 0;
  logic [23:0] source_data;
  logic source_valid, source_ready, source_last;
  logic [9:0] active_width, active_height;
  logic frame_start;
  logic [31:0] neighborhood_data;
  logic neighborhood_valid, neighborhood_ready, neighborhood_last;
  integer errors = 0;
  integer checks = 0;

  sr_lut_line_buffer_dynamic #(
    .MAX_FRAME_WIDTH(480), .MAX_FRAME_HEIGHT(320)
  ) dut (
    .clk_i(clk), .resetn_i(resetn),
    .s_axis_tdata_i(source_data), .s_axis_tvalid_i(source_valid),
    .s_axis_tready_o(source_ready), .s_axis_tlast_i(source_last),
    .active_width_i(active_width), .active_height_i(active_height),
    .frame_start_i(frame_start),
    .neighborhood_data_o(neighborhood_data),
    .neighborhood_valid_o(neighborhood_valid),
    .neighborhood_ready_i(neighborhood_ready),
    .neighborhood_last_o(neighborhood_last)
  );

  always #5 clk = ~clk;

  function automatic logic [23:0] pixel_at(
    input integer frame_id, input integer row, input integer column
  );
    logic [7:0] red, green, blue;
    begin
      red = frame_id * 53 + row * 17 + column * 3;
      green = frame_id * 29 + row * 7 + column * 19;
      blue = frame_id * 11 + row * 31 + column * 5;
      pixel_at = {blue, green, red};
    end
  endfunction

  function automatic logic [31:0] expected_neighborhood(
    input integer frame_id, input integer width, input integer height,
    input integer output_index
  );
    integer pixel_index, direction, channel, row, column;
    integer lateral_column, neighbor_row;
    logic [23:0] p0, p1, p2, p3, lateral, vertical, diagonal;
    logic [7:0] v0, v1, v2, v3;
    begin
      channel = output_index % 3;
      direction = (output_index / 3) % 4;
      pixel_index = output_index / 12;
      row = pixel_index / width;
      column = pixel_index % width;
      lateral_column = (direction == 0 || direction == 3)
          ? ((column == width - 1) ? width - 2 : column + 1)
          : ((column == 0) ? 1 : column - 1);
      neighbor_row = (direction < 2)
          ? ((row == height - 1) ? height - 2 : row + 1)
          : ((row == 0) ? 1 : row - 1);
      p0 = pixel_at(frame_id, row, column);
      lateral = pixel_at(frame_id, row, lateral_column);
      vertical = pixel_at(frame_id, neighbor_row, column);
      diagonal = pixel_at(frame_id, neighbor_row, lateral_column);
      if (direction == 0 || direction == 2) begin
        p1 = lateral; p2 = vertical;
      end else begin
        p1 = vertical; p2 = lateral;
      end
      p3 = diagonal;
      case (channel)
        0: begin v0=p0[7:0]; v1=p1[7:0]; v2=p2[7:0]; v3=p3[7:0]; end
        1: begin v0=p0[15:8]; v1=p1[15:8]; v2=p2[15:8]; v3=p3[15:8]; end
        default: begin v0=p0[23:16]; v1=p1[23:16]; v2=p2[23:16]; v3=p3[23:16]; end
      endcase
      expected_neighborhood = {v3, v2, v1, v0};
    end
  endfunction

  task automatic run_frame(
    input integer width, input integer height, input integer frame_id
  );
    integer input_index, output_index, cycles, row, column, output_words;
    logic [31:0] expected, accepted_output;
    logic accepted_input, accepted_output_valid, accepted_output_last;
    begin
      active_width = width;
      active_height = height;
      frame_start = 1'b1;
      @(posedge clk); #1;
      frame_start = 1'b0;
      input_index = 0;
      output_index = 0;
      cycles = 0;
      output_words = width * height * 12;
      while (input_index < width * height || output_index < output_words) begin
        @(negedge clk);
        neighborhood_ready = (cycles % 7 != 2);
        if (input_index < width * height) begin
          row = input_index / width;
          column = input_index % width;
          source_data = pixel_at(frame_id, row, column);
          source_valid = 1'b1;
          source_last = (input_index == width * height - 1);
        end else begin
          source_valid = 1'b0;
          source_last = 1'b0;
        end
        #1;
        accepted_input = source_valid && source_ready;
        accepted_output_valid = neighborhood_valid && neighborhood_ready;
        accepted_output = neighborhood_data;
        accepted_output_last = neighborhood_last;
        @(posedge clk); #1;
        if (accepted_input) input_index = input_index + 1;
        if (accepted_output_valid) begin
          expected = expected_neighborhood(frame_id, width, height, output_index);
          if (accepted_output !== expected) begin
            errors = errors + 1;
            if (errors < 10) $display("dynamic neighborhood mismatch frame=%0d index=%0d actual=%08x expected=%08x", frame_id, output_index, accepted_output, expected);
          end
          if (accepted_output_last !== (output_index == output_words - 1)) begin
            errors = errors + 1;
          end
          output_index = output_index + 1;
          checks = checks + 1;
        end
        cycles = cycles + 1;
        if (cycles > (width * height * 24 + 10000))
          $fatal(1, "dynamic line buffer timeout width=%0d height=%0d", width, height);
      end
    end
  endtask

  initial begin
    source_data = 0; source_valid = 0; source_last = 0;
    active_width = 0; active_height = 0; frame_start = 0;
    neighborhood_ready = 0;
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
    if (errors != 0) $fatal(1, "DYNAMIC_LINE_BUFFER_FAIL errors=%0d", errors);
    $display("DYNAMIC_LINE_BUFFER_PASS frames=13 checks=%0d", checks);
    $finish;
  end
endmodule
