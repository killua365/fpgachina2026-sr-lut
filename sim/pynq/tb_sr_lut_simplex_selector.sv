`timescale 1ns/1ps

module tb_sr_lut_simplex_selector;

  import sr_lut_pkg::*;

  localparam int unsigned CORE_CASES = 10041;
  localparam int unsigned MAX_PRINTED_ERRORS = 20;
  localparam int unsigned MASK_BUS_BITS =
      SIMPLEX_VERTEX_COUNT * SIMPLEX_MASK_BITS;
  localparam int unsigned WEIGHT_BUS_BITS =
      SIMPLEX_VERTEX_COUNT * SIMPLEX_WEIGHT_BITS;

  logic [(NEIGHBOR_COUNT * PIXEL_BITS) - 1:0] x2_pixels;
  logic [(NEIGHBOR_COUNT * X2_LOWER_BITS) - 1:0] x2_lower_unused;
  logic [(NEIGHBOR_COUNT * X2_SAMPLE_BITS) - 1:0] x2_quant_remainders;
  logic [(NEIGHBOR_COUNT * X2_SAMPLE_BITS) - 1:0] x2_direct_remainders;
  logic [(NEIGHBOR_COUNT * X2_SAMPLE_BITS) - 1:0] x2_selector_remainders;
  logic x2_use_quantizer;
  logic [MASK_BUS_BITS - 1:0] x2_masks;
  logic [WEIGHT_BUS_BITS - 1:0] x2_weights;

  logic [(NEIGHBOR_COUNT * PIXEL_BITS) - 1:0] x4_pixels;
  logic [(NEIGHBOR_COUNT * X4_LOWER_BITS) - 1:0] x4_lower_unused;
  logic [(NEIGHBOR_COUNT * X4_SAMPLE_BITS) - 1:0] x4_quant_remainders;
  logic [(NEIGHBOR_COUNT * X4_SAMPLE_BITS) - 1:0] x4_direct_remainders;
  logic [(NEIGHBOR_COUNT * X4_SAMPLE_BITS) - 1:0] x4_selector_remainders;
  logic x4_use_quantizer;
  logic [MASK_BUS_BITS - 1:0] x4_masks;
  logic [WEIGHT_BUS_BITS - 1:0] x4_weights;

  logic [31:0] input_vectors [0:CORE_CASES - 1];
  logic [31:0] remainder_vectors [0:CORE_CASES - 1];
  logic [MASK_BUS_BITS - 1:0] mask_vectors [0:CORE_CASES - 1];
  logic [WEIGHT_BUS_BITS - 1:0] weight_vectors [0:CORE_CASES - 1];

  string vector_root;
  integer error_count;
  integer evaluation_count;
  integer negative_case_count_x2;
  integer negative_case_count_x4;

  assign x2_selector_remainders =
      x2_use_quantizer ? x2_quant_remainders : x2_direct_remainders;
  assign x4_selector_remainders =
      x4_use_quantizer ? x4_quant_remainders : x4_direct_remainders;

  sr_lut_quantizer #(
    .SAMPLE_BITS(X2_SAMPLE_BITS),
    .LEVELS(X2_LEVELS),
    .LOWER_BITS(X2_LOWER_BITS)
  ) quantizer_x2 (
    .pixels_i(x2_pixels),
    .lower_o(x2_lower_unused),
    .remainder_o(x2_quant_remainders)
  );

  sr_lut_simplex_selector #(
    .SAMPLE_BITS(X2_SAMPLE_BITS),
    .Q(X2_Q)
  ) dut_x2 (
    .remainders_i(x2_selector_remainders),
    .masks_o(x2_masks),
    .weights_o(x2_weights)
  );

  sr_lut_quantizer #(
    .SAMPLE_BITS(X4_SAMPLE_BITS),
    .LEVELS(X4_LEVELS),
    .LOWER_BITS(X4_LOWER_BITS)
  ) quantizer_x4 (
    .pixels_i(x4_pixels),
    .lower_o(x4_lower_unused),
    .remainder_o(x4_quant_remainders)
  );

  sr_lut_simplex_selector #(
    .SAMPLE_BITS(X4_SAMPLE_BITS),
    .Q(X4_Q)
  ) dut_x4 (
    .remainders_i(x4_selector_remainders),
    .masks_o(x4_masks),
    .weights_o(x4_weights)
  );

  function automatic logic has_negative_weight(
    input logic [WEIGHT_BUS_BITS - 1:0] packed_weights
  );
    integer vertex;
    begin
      has_negative_weight = 1'b0;
      for (vertex = 0; vertex < SIMPLEX_VERTEX_COUNT; vertex++) begin
        if ($signed(packed_weights[
              (vertex * SIMPLEX_WEIGHT_BITS) +: SIMPLEX_WEIGHT_BITS
            ]) < 0) begin
          has_negative_weight = 1'b1;
        end
      end
    end
  endfunction

  task automatic record_invariant_error(
    input string configuration,
    input string phase,
    input integer case_index,
    input string reason
  );
    begin
      error_count = error_count + 1;
      if (error_count <= MAX_PRINTED_ERRORS) begin
        $display(
          "ERROR config=%s phase=%s case=%0d invariant=%s",
          configuration, phase, case_index, reason
        );
      end
    end
  endtask

  task automatic check_result(
    input string configuration,
    input string phase,
    input integer case_index,
    input integer q,
    input logic [MASK_BUS_BITS - 1:0] expected_masks,
    input logic [WEIGHT_BUS_BITS - 1:0] expected_weights,
    input logic [MASK_BUS_BITS - 1:0] actual_masks,
    input logic [WEIGHT_BUS_BITS - 1:0] actual_weights
  );
    integer vertex;
    integer weight_sum;
    logic [SIMPLEX_MASK_BITS - 1:0] previous_mask;
    logic [SIMPLEX_MASK_BITS - 1:0] current_mask;
    logic [SIMPLEX_MASK_BITS - 1:0] added_mask;
    logic signed [SIMPLEX_WEIGHT_BITS - 1:0] weight_value;
    begin
      evaluation_count = evaluation_count + 1;

      if ((actual_masks !== expected_masks) ||
          (actual_weights !== expected_weights)) begin
        error_count = error_count + 1;
        if (error_count <= MAX_PRINTED_ERRORS) begin
          $display(
            "ERROR config=%s phase=%s case=%0d masks_expected=%05h masks_actual=%05h weights_expected=%010h weights_actual=%010h",
            configuration, phase, case_index,
            expected_masks, actual_masks, expected_weights, actual_weights
          );
        end
      end

      if (actual_masks[0 +: SIMPLEX_MASK_BITS] !== 4'h0) begin
        record_invariant_error(configuration, phase, case_index,
                               "first_mask_is_not_0000");
      end
      if (actual_masks[(4 * SIMPLEX_MASK_BITS) +: SIMPLEX_MASK_BITS]
          !== 4'hF) begin
        record_invariant_error(configuration, phase, case_index,
                               "last_mask_is_not_1111");
      end

      previous_mask = actual_masks[0 +: SIMPLEX_MASK_BITS];
      for (vertex = 1; vertex < SIMPLEX_VERTEX_COUNT; vertex++) begin
        current_mask = actual_masks[
          (vertex * SIMPLEX_MASK_BITS) +: SIMPLEX_MASK_BITS
        ];
        if ((current_mask & previous_mask) !== previous_mask) begin
          record_invariant_error(configuration, phase, case_index,
                                 "mask_transition_removed_a_bit");
        end
        added_mask = current_mask & ~previous_mask;
        case (added_mask)
          4'h1, 4'h2, 4'h4, 4'h8: begin end
          default: record_invariant_error(
            configuration, phase, case_index,
            "mask_transition_did_not_add_exactly_one_bit"
          );
        endcase
        previous_mask = current_mask;
      end

      weight_sum = 0;
      for (vertex = 0; vertex < SIMPLEX_VERTEX_COUNT; vertex++) begin
        weight_value = $signed(actual_weights[
          (vertex * SIMPLEX_WEIGHT_BITS) +: SIMPLEX_WEIGHT_BITS
        ]);
        weight_sum = weight_sum + weight_value;
      end
      if (weight_sum != q) begin
        record_invariant_error(configuration, phase, case_index,
                               "signed_weight_sum_is_not_q");
      end
    end
  endtask

  task automatic load_vectors(input string configuration);
    begin
      $readmemh($sformatf("%s/%s/inputs.mem", vector_root, configuration),
                input_vectors);
      $readmemh(
        $sformatf("%s/%s/remainders.mem", vector_root, configuration),
        remainder_vectors
      );
      $readmemh($sformatf("%s/%s/masks.mem", vector_root, configuration),
                mask_vectors);
      $readmemh($sformatf("%s/%s/weights.mem", vector_root, configuration),
                weight_vectors);
    end
  endtask

  task automatic run_x2_tests;
    integer case_index;
    integer lane;
    begin
      $display("E2 x2_4bit: loading 10041 simplex cases");
      load_vectors("x2_4bit");
      x2_use_quantizer = 1'b0;

      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x2_direct_remainders = '0;
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          if ((remainder_vectors[case_index][(lane * 8) +: 8]
               >> X2_SAMPLE_BITS) != 0) begin
            $fatal(1, "x2 remainder vector exceeds SAMPLE_BITS");
          end
          x2_direct_remainders[(lane * X2_SAMPLE_BITS) +: X2_SAMPLE_BITS] =
              remainder_vectors[case_index][(lane * 8) +: X2_SAMPLE_BITS];
        end
        #1;
        check_result(
          "x2_4bit", "direct_remainders", case_index, X2_Q,
          mask_vectors[case_index], weight_vectors[case_index],
          x2_masks, x2_weights
        );
        if (has_negative_weight(weight_vectors[case_index])) begin
          negative_case_count_x2 = negative_case_count_x2 + 1;
        end
      end

      x2_use_quantizer = 1'b1;
      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x2_pixels = input_vectors[case_index];
        x2_direct_remainders = '0;
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          x2_direct_remainders[(lane * X2_SAMPLE_BITS) +: X2_SAMPLE_BITS] =
              remainder_vectors[case_index][(lane * 8) +: X2_SAMPLE_BITS];
        end
        #1;
        if (x2_quant_remainders !== x2_direct_remainders) begin
          record_invariant_error(
            "x2_4bit", "quantizer_chain", case_index,
            "E1_to_E2_remainder_packing_mismatch"
          );
        end
        check_result(
          "x2_4bit", "quantizer_chain", case_index, X2_Q,
          mask_vectors[case_index], weight_vectors[case_index],
          x2_masks, x2_weights
        );
      end
    end
  endtask

  task automatic run_x4_tests;
    integer case_index;
    integer lane;
    begin
      $display("E2 x4_5bit: loading 10041 simplex cases");
      load_vectors("x4_5bit");
      x4_use_quantizer = 1'b0;

      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x4_direct_remainders = '0;
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          if ((remainder_vectors[case_index][(lane * 8) +: 8]
               >> X4_SAMPLE_BITS) != 0) begin
            $fatal(1, "x4 remainder vector exceeds SAMPLE_BITS");
          end
          x4_direct_remainders[(lane * X4_SAMPLE_BITS) +: X4_SAMPLE_BITS] =
              remainder_vectors[case_index][(lane * 8) +: X4_SAMPLE_BITS];
        end
        #1;
        check_result(
          "x4_5bit", "direct_remainders", case_index, X4_Q,
          mask_vectors[case_index], weight_vectors[case_index],
          x4_masks, x4_weights
        );
        if (has_negative_weight(weight_vectors[case_index])) begin
          negative_case_count_x4 = negative_case_count_x4 + 1;
        end
      end

      x4_use_quantizer = 1'b1;
      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x4_pixels = input_vectors[case_index];
        x4_direct_remainders = '0;
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          x4_direct_remainders[(lane * X4_SAMPLE_BITS) +: X4_SAMPLE_BITS] =
              remainder_vectors[case_index][(lane * 8) +: X4_SAMPLE_BITS];
        end
        #1;
        if (x4_quant_remainders !== x4_direct_remainders) begin
          record_invariant_error(
            "x4_5bit", "quantizer_chain", case_index,
            "E1_to_E2_remainder_packing_mismatch"
          );
        end
        check_result(
          "x4_5bit", "quantizer_chain", case_index, X4_Q,
          mask_vectors[case_index], weight_vectors[case_index],
          x4_masks, x4_weights
        );
      end
    end
  endtask

  initial begin
    error_count = 0;
    evaluation_count = 0;
    negative_case_count_x2 = 0;
    negative_case_count_x4 = 0;
    x2_pixels = '0;
    x2_direct_remainders = '0;
    x2_use_quantizer = 1'b0;
    x4_pixels = '0;
    x4_direct_remainders = '0;
    x4_use_quantizer = 1'b0;

    if (!$value$plusargs("VECTOR_ROOT=%s", vector_root)) begin
      vector_root = "data/golden/rtl_core";
    end

    if ((SIMPLEX_MASK_BITS != 4) || (SIMPLEX_VERTEX_COUNT != 5) ||
        (SIMPLEX_WEIGHT_BITS != 7)) begin
      $fatal(1, "Invalid simplex constants in sr_lut_pkg");
    end

    run_x2_tests();
    run_x4_tests();

    if ((negative_case_count_x2 == 0) || (negative_case_count_x4 == 0)) begin
      $fatal(1, "Negative-weight coverage is missing");
    end
    if (error_count != 0) begin
      $fatal(
        1,
        "E2_SIMPLEX_FAIL errors=%0d selector_evaluations=%0d",
        error_count,
        evaluation_count
      );
    end

    $display(
      "E2_SIMPLEX_PASS configurations=2 vector_rows=%0d selector_evaluations=%0d negative_cases_x2=%0d negative_cases_x4=%0d",
      2 * CORE_CASES,
      evaluation_count,
      negative_case_count_x2,
      negative_case_count_x4
    );
    $finish;
  end

endmodule
