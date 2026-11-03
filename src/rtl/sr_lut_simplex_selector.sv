`timescale 1ns/1ps

module sr_lut_simplex_selector #(
  parameter int unsigned SAMPLE_BITS = sr_lut_pkg::X2_SAMPLE_BITS,
  parameter int unsigned Q = 1 << SAMPLE_BITS,
  parameter int unsigned LANES = sr_lut_pkg::NEIGHBOR_COUNT,
  parameter int unsigned MASK_BITS = sr_lut_pkg::SIMPLEX_MASK_BITS,
  parameter int unsigned VERTICES = sr_lut_pkg::SIMPLEX_VERTEX_COUNT,
  parameter int unsigned WEIGHT_BITS = sr_lut_pkg::SIMPLEX_WEIGHT_BITS
) (
  input  logic [(LANES * SAMPLE_BITS) - 1:0] remainders_i,
  output logic [(VERTICES * MASK_BITS) - 1:0] masks_o,
  output logic [(VERTICES * WEIGHT_BITS) - 1:0] weights_o
);

  localparam int unsigned MASK_BUS_BITS = VERTICES * MASK_BITS;
  localparam int unsigned WEIGHT_BUS_BITS = VERTICES * WEIGHT_BITS;
  localparam int unsigned RESULT_BITS = MASK_BUS_BITS + WEIGHT_BUS_BITS;

  logic signed [WEIGHT_BITS - 1:0] fa;
  logic signed [WEIGHT_BITS - 1:0] fb;
  logic signed [WEIGHT_BITS - 1:0] fc;
  logic signed [WEIGHT_BITS - 1:0] fd;
  logic signed [WEIGHT_BITS - 1:0] q_signed;

  function automatic logic [RESULT_BITS - 1:0] pack_result(
    input logic [MASK_BITS - 1:0] mask1,
    input logic [MASK_BITS - 1:0] mask2,
    input logic [MASK_BITS - 1:0] mask3,
    input logic signed [WEIGHT_BITS - 1:0] weight0,
    input logic signed [WEIGHT_BITS - 1:0] weight1,
    input logic signed [WEIGHT_BITS - 1:0] weight2,
    input logic signed [WEIGHT_BITS - 1:0] weight3,
    input logic signed [WEIGHT_BITS - 1:0] weight4
  );
    logic [MASK_BUS_BITS - 1:0] packed_masks;
    logic [WEIGHT_BUS_BITS - 1:0] packed_weights;
    begin
      packed_masks = '0;
      packed_weights = '0;

      packed_masks[(0 * MASK_BITS) +: MASK_BITS] = '0;
      packed_masks[(1 * MASK_BITS) +: MASK_BITS] = mask1;
      packed_masks[(2 * MASK_BITS) +: MASK_BITS] = mask2;
      packed_masks[(3 * MASK_BITS) +: MASK_BITS] = mask3;
      packed_masks[(4 * MASK_BITS) +: MASK_BITS] = {MASK_BITS{1'b1}};

      packed_weights[(0 * WEIGHT_BITS) +: WEIGHT_BITS] = weight0;
      packed_weights[(1 * WEIGHT_BITS) +: WEIGHT_BITS] = weight1;
      packed_weights[(2 * WEIGHT_BITS) +: WEIGHT_BITS] = weight2;
      packed_weights[(3 * WEIGHT_BITS) +: WEIGHT_BITS] = weight3;
      packed_weights[(4 * WEIGHT_BITS) +: WEIGHT_BITS] = weight4;

      pack_result = {packed_weights, packed_masks};
    end
  endfunction

  always_comb begin
    fa = $signed({
      {(WEIGHT_BITS - SAMPLE_BITS){1'b0}},
      remainders_i[(0 * SAMPLE_BITS) +: SAMPLE_BITS]
    });
    fb = $signed({
      {(WEIGHT_BITS - SAMPLE_BITS){1'b0}},
      remainders_i[(1 * SAMPLE_BITS) +: SAMPLE_BITS]
    });
    fc = $signed({
      {(WEIGHT_BITS - SAMPLE_BITS){1'b0}},
      remainders_i[(2 * SAMPLE_BITS) +: SAMPLE_BITS]
    });
    fd = $signed({
      {(WEIGHT_BITS - SAMPLE_BITS){1'b0}},
      remainders_i[(3 * SAMPLE_BITS) +: SAMPLE_BITS]
    });
    q_signed = Q;

    masks_o = '0;
    weights_o = '0;

    if (fa > fb) begin
      if (fb > fc) begin
        if (fc > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h8, 4'hC, 4'hE,
            q_signed - fa, fa - fb, fb - fc, fc - fd, fd
          );
        end else if (fb > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h8, 4'hC, 4'hD,
            q_signed - fa, fa - fb, fb - fd, fd - fc, fc
          );
        end else if (fa > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h8, 4'h9, 4'hD,
            q_signed - fa, fa - fd, fd - fb, fb - fc, fc
          );
        end else begin
          {weights_o, masks_o} = pack_result(
            4'h1, 4'h9, 4'hD,
            q_signed - fd, fd - fa, fa - fb, fb - fc, fc
          );
        end
      end else if (fa > fc) begin
        if (fb > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h8, 4'hA, 4'hE,
            q_signed - fa, fa - fc, fc - fb, fb - fd, fd
          );
        end else if (fc > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h8, 4'hA, 4'hB,
            q_signed - fa, fa - fc, fc - fd, fd - fb, fb
          );
        end else if (fa > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h8, 4'h9, 4'hB,
            q_signed - fa, fa - fd, fd - fc, fc - fb, fb
          );
        end else begin
          {weights_o, masks_o} = pack_result(
            4'h1, 4'h9, 4'hB,
            q_signed - fd, fd - fa, fa - fc, fc - fb, fb
          );
        end
      end else begin
        if (fb > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h2, 4'hA, 4'hE,
            q_signed - fc, fc - fa, fa - fb, fb - fd, fd
          );
        end else if (fc > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h2, 4'hA, 4'hB,
            q_signed - fc, fc - fa, fa - fd, fd - fb, fb
          );
        end else if (fa > fd) begin
          {weights_o, masks_o} = pack_result(
            4'h2, 4'h3, 4'hB,
            q_signed - fc, fc - fd, fd - fa, fa - fb, fb
          );
        end else begin
          {weights_o, masks_o} = pack_result(
            4'h1, 4'h3, 4'hB,
            q_signed - fd, fd - fc, fc - fa, fa - fb, fb
          );
        end
      end
    end else if (fa > fc) begin
      if (fc > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h4, 4'hC, 4'hE,
          q_signed - fb, fb - fa, fa - fc, fc - fd, fd
        );
      end else if (fa > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h4, 4'hC, 4'hD,
          q_signed - fb, fb - fa, fa - fd, fd - fc, fc
        );
      end else if (fb > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h4, 4'h5, 4'hD,
          q_signed - fb, fb - fd, fd - fa, fa - fc, fc
        );
      end else begin
        {weights_o, masks_o} = pack_result(
          4'h1, 4'h5, 4'hD,
          q_signed - fd, fd - fb, fb - fa, fa - fc, fc
        );
      end
    end else if (fb > fc) begin
      if (fa > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h4, 4'h6, 4'hE,
          q_signed - fb, fb - fc, fc - fa, fa - fd, fd
        );
      end else if (fc > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h4, 4'h6, 4'h7,
          q_signed - fb, fb - fc, fc - fd, fd - fa, fa
        );
      end else if (fb > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h4, 4'h5, 4'h7,
          q_signed - fb, fb - fd, fd - fc, fc - fa, fa
        );
      end else begin
        {weights_o, masks_o} = pack_result(
          4'h1, 4'h5, 4'h7,
          q_signed - fd, fd - fb, fb - fc, fc - fa, fa
        );
      end
    end else begin
      if (fa > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h2, 4'h6, 4'hE,
          q_signed - fc, fc - fb, fb - fa, fa - fd, fd
        );
      end else if (fb > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h2, 4'h6, 4'h7,
          q_signed - fc, fc - fb, fb - fd, fd - fa, fa
        );
      end else if (fc > fd) begin
        {weights_o, masks_o} = pack_result(
          4'h2, 4'h3, 4'h7,
          q_signed - fc, fc - fd, fd - fb, fb - fa, fa
        );
      end else begin
        {weights_o, masks_o} = pack_result(
          4'h1, 4'h3, 4'h7,
          q_signed - fd, fd - fc, fc - fb, fb - fa, fa
        );
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((LANES != 4) || (MASK_BITS != 4) || (VERTICES != 5)) begin
      $fatal(1, "Model-S simplex requires 4 lanes, 4-bit masks, and 5 vertices");
    end
    if ((SAMPLE_BITS == 0) || (SAMPLE_BITS >= WEIGHT_BITS)) begin
      $fatal(1, "SAMPLE_BITS must fit inside signed WEIGHT_BITS");
    end
    if ((WEIGHT_BITS != 7) || (Q != (1 << SAMPLE_BITS)) ||
        (Q > ((1 << (WEIGHT_BITS - 1)) - 1))) begin
      $fatal(1, "Invalid simplex weight configuration");
    end
  end
`endif

endmodule
