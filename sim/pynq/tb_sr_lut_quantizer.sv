`timescale 1ns/1ps

module tb_sr_lut_quantizer;

  import sr_lut_pkg::*;

  localparam int unsigned QUANTIZER_CASES = 256;
  localparam int unsigned CORE_CASES = 10041;
  localparam int unsigned MAX_PRINTED_ERRORS = 20;

  logic [(NEIGHBOR_COUNT * PIXEL_BITS) - 1:0] x2_pixels;
  logic [(NEIGHBOR_COUNT * X2_LOWER_BITS) - 1:0] x2_lower;
  logic [(NEIGHBOR_COUNT * X2_SAMPLE_BITS) - 1:0] x2_remainders;

  logic [(NEIGHBOR_COUNT * PIXEL_BITS) - 1:0] x4_pixels;
  logic [(NEIGHBOR_COUNT * X4_LOWER_BITS) - 1:0] x4_lower;
  logic [(NEIGHBOR_COUNT * X4_SAMPLE_BITS) - 1:0] x4_remainders;

  logic [31:0] quantizer_vectors [0:QUANTIZER_CASES - 1];
  logic [31:0] input_vectors [0:CORE_CASES - 1];
  logic [31:0] lower_vectors [0:CORE_CASES - 1];
  logic [31:0] remainder_vectors [0:CORE_CASES - 1];

  string vector_root;
  integer error_count;
  integer lane_check_count;

  sr_lut_quantizer #(
    .SAMPLE_BITS(X2_SAMPLE_BITS),
    .LEVELS(X2_LEVELS),
    .LOWER_BITS(X2_LOWER_BITS)
  ) dut_x2 (
    .pixels_i(x2_pixels),
    .lower_o(x2_lower),
    .remainder_o(x2_remainders)
  );

  sr_lut_quantizer #(
    .SAMPLE_BITS(X4_SAMPLE_BITS),
    .LEVELS(X4_LEVELS),
    .LOWER_BITS(X4_LOWER_BITS)
  ) dut_x4 (
    .pixels_i(x4_pixels),
    .lower_o(x4_lower),
    .remainder_o(x4_remainders)
  );

  task automatic print_mismatch(
    input string configuration,
    input string phase,
    input integer case_index,
    input integer lane,
    input logic [7:0] input_pixel,
    input logic [7:0] expected_lower,
    input logic [7:0] actual_lower,
    input logic [7:0] expected_remainder,
    input logic [7:0] actual_remainder
  );
    begin
      error_count = error_count + 1;
      if (error_count <= MAX_PRINTED_ERRORS) begin
        $display(
          "ERROR config=%s phase=%s case=%0d lane=%0d pixel=%0d lower_expected=%0d lower_actual=%0d remainder_expected=%0d remainder_actual=%0d",
          configuration,
          phase,
          case_index,
          lane,
          input_pixel,
          expected_lower,
          actual_lower,
          expected_remainder,
          actual_remainder
        );
      end
    end
  endtask

  task automatic check_x2_lane(
    input string phase,
    input integer case_index,
    input integer lane,
    input logic [7:0] input_pixel,
    input logic [7:0] expected_lower,
    input logic [7:0] expected_remainder
  );
    logic [7:0] actual_lower;
    logic [7:0] actual_remainder;
    begin
      actual_lower = '0;
      actual_remainder = '0;
      actual_lower[X2_LOWER_BITS - 1:0] =
          x2_lower[(lane * X2_LOWER_BITS) +: X2_LOWER_BITS];
      actual_remainder[X2_SAMPLE_BITS - 1:0] =
          x2_remainders[(lane * X2_SAMPLE_BITS) +: X2_SAMPLE_BITS];
      lane_check_count = lane_check_count + 1;
      if ((actual_lower !== expected_lower) ||
          (actual_remainder !== expected_remainder)) begin
        print_mismatch(
          "x2_4bit", phase, case_index, lane, input_pixel,
          expected_lower, actual_lower, expected_remainder, actual_remainder
        );
      end
    end
  endtask

  task automatic check_x4_lane(
    input string phase,
    input integer case_index,
    input integer lane,
    input logic [7:0] input_pixel,
    input logic [7:0] expected_lower,
    input logic [7:0] expected_remainder
  );
    logic [7:0] actual_lower;
    logic [7:0] actual_remainder;
    begin
      actual_lower = '0;
      actual_remainder = '0;
      actual_lower[X4_LOWER_BITS - 1:0] =
          x4_lower[(lane * X4_LOWER_BITS) +: X4_LOWER_BITS];
      actual_remainder[X4_SAMPLE_BITS - 1:0] =
          x4_remainders[(lane * X4_SAMPLE_BITS) +: X4_SAMPLE_BITS];
      lane_check_count = lane_check_count + 1;
      if ((actual_lower !== expected_lower) ||
          (actual_remainder !== expected_remainder)) begin
        print_mismatch(
          "x4_5bit", phase, case_index, lane, input_pixel,
          expected_lower, actual_lower, expected_remainder, actual_remainder
        );
      end
    end
  endtask

  task automatic run_x2_tests;
    integer case_index;
    integer lane;
    logic [7:0] expected_lower;
    logic [7:0] expected_upper;
    logic [7:0] expected_remainder;
    begin
      $display("E1 x2_4bit: loading vectors from %s/x2_4bit", vector_root);
      $readmemh($sformatf("%s/x2_4bit/quantizer.mem", vector_root),
                quantizer_vectors);

      for (case_index = 0; case_index < QUANTIZER_CASES; case_index++) begin
        x2_pixels = {NEIGHBOR_COUNT{quantizer_vectors[case_index][7:0]}};
        expected_lower = quantizer_vectors[case_index][15:8];
        expected_upper = quantizer_vectors[case_index][23:16];
        expected_remainder = quantizer_vectors[case_index][31:24];
        #1;

        if (expected_upper !== (expected_lower + 8'd1)) begin
          $fatal(1, "Invalid x2 quantizer vector at case %0d", case_index);
        end
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          check_x2_lane(
            "all_u8", case_index, lane,
            quantizer_vectors[case_index][7:0],
            expected_lower, expected_remainder
          );
        end
      end

      $readmemh($sformatf("%s/x2_4bit/inputs.mem", vector_root),
                input_vectors);
      $readmemh($sformatf("%s/x2_4bit/lower.mem", vector_root),
                lower_vectors);
      $readmemh($sformatf("%s/x2_4bit/remainders.mem", vector_root),
                remainder_vectors);

      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x2_pixels = input_vectors[case_index];
        #1;
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          check_x2_lane(
            "core_cases", case_index, lane,
            input_vectors[case_index][(lane * 8) +: 8],
            lower_vectors[case_index][(lane * 8) +: 8],
            remainder_vectors[case_index][(lane * 8) +: 8]
          );
        end
      end
    end
  endtask

  task automatic run_x4_tests;
    integer case_index;
    integer lane;
    logic [7:0] expected_lower;
    logic [7:0] expected_upper;
    logic [7:0] expected_remainder;
    begin
      $display("E1 x4_5bit: loading vectors from %s/x4_5bit", vector_root);
      $readmemh($sformatf("%s/x4_5bit/quantizer.mem", vector_root),
                quantizer_vectors);

      for (case_index = 0; case_index < QUANTIZER_CASES; case_index++) begin
        x4_pixels = {NEIGHBOR_COUNT{quantizer_vectors[case_index][7:0]}};
        expected_lower = quantizer_vectors[case_index][15:8];
        expected_upper = quantizer_vectors[case_index][23:16];
        expected_remainder = quantizer_vectors[case_index][31:24];
        #1;

        if (expected_upper !== (expected_lower + 8'd1)) begin
          $fatal(1, "Invalid x4 quantizer vector at case %0d", case_index);
        end
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          check_x4_lane(
            "all_u8", case_index, lane,
            quantizer_vectors[case_index][7:0],
            expected_lower, expected_remainder
          );
        end
      end

      $readmemh($sformatf("%s/x4_5bit/inputs.mem", vector_root),
                input_vectors);
      $readmemh($sformatf("%s/x4_5bit/lower.mem", vector_root),
                lower_vectors);
      $readmemh($sformatf("%s/x4_5bit/remainders.mem", vector_root),
                remainder_vectors);

      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x4_pixels = input_vectors[case_index];
        #1;
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          check_x4_lane(
            "core_cases", case_index, lane,
            input_vectors[case_index][(lane * 8) +: 8],
            lower_vectors[case_index][(lane * 8) +: 8],
            remainder_vectors[case_index][(lane * 8) +: 8]
          );
        end
      end
    end
  endtask

  initial begin
    error_count = 0;
    lane_check_count = 0;
    x2_pixels = '0;
    x4_pixels = '0;

    if (!$value$plusargs("VECTOR_ROOT=%s", vector_root)) begin
      vector_root = "data/golden/rtl_core";
    end

    if ((PIXEL_BITS != 8) || (NEIGHBOR_COUNT != 4) ||
        (X2_SCALE != 2) || (X2_SAMPLE_BITS != 4) || (X2_Q != 16) ||
        (X2_LEVELS != 17) || (X2_LUT_DEPTH != 83521) ||
        (X2_LOWER_BITS != 5) || (X2_ADDR_BITS != 17) ||
        (X2_OUTPUTS_PER_ENTRY != 4) || (X2_LUT_WORD_BITS != 32)) begin
      $fatal(1, "Invalid x2 constants in sr_lut_pkg");
    end
    if ((X4_SCALE != 4) || (X4_SAMPLE_BITS != 5) || (X4_Q != 32) ||
        (X4_LEVELS != 9) || (X4_LUT_DEPTH != 6561) ||
        (X4_LOWER_BITS != 4) || (X4_ADDR_BITS != 13) ||
        (X4_OUTPUTS_PER_ENTRY != 16) || (X4_LUT_WORD_BITS != 128)) begin
      $fatal(1, "Invalid x4 constants in sr_lut_pkg");
    end

    run_x2_tests();
    run_x4_tests();

    if (error_count != 0) begin
      $fatal(
        1,
        "E1_QUANTIZER_FAIL errors=%0d lane_checks=%0d",
        error_count,
        lane_check_count
      );
    end

    $display(
      "E1_QUANTIZER_PASS configurations=2 vector_rows=%0d lane_checks=%0d",
      2 * (QUANTIZER_CASES + CORE_CASES),
      lane_check_count
    );
    $finish;
  end

endmodule
