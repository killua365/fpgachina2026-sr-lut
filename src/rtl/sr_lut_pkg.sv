`timescale 1ns/1ps

package sr_lut_pkg;

  localparam int unsigned PIXEL_BITS = 8;
  localparam int unsigned NEIGHBOR_COUNT = 4;
  localparam int unsigned LUT_VALUE_BITS = 8;
  // Exhaustive x4 remainder analysis gives signed weights in [-29, 32]
  // with a maximum absolute sum of 90. For signed int8 LUT values this
  // bounds one interpolated numerator to [-11491, 11459], and the sum of
  // four directions to [-45964, 45836].
  localparam int unsigned NUMERATOR_BITS = 15;
  localparam int unsigned FUSION_BITS = 17;
  localparam int unsigned SIMPLEX_MASK_BITS = 4;
  localparam int unsigned SIMPLEX_VERTEX_COUNT = 5;
  localparam int unsigned SIMPLEX_WEIGHT_BITS = 7;

  localparam int unsigned X2_SCALE = 2;
  localparam int unsigned X2_SAMPLE_BITS = 4;
  localparam int unsigned X2_Q = 1 << X2_SAMPLE_BITS;
  localparam int unsigned X2_LEVELS = (1 << (PIXEL_BITS - X2_SAMPLE_BITS)) + 1;
  localparam int unsigned X2_LUT_DEPTH =
      X2_LEVELS * X2_LEVELS * X2_LEVELS * X2_LEVELS;
  localparam int unsigned X2_LOWER_BITS = $clog2(X2_LEVELS);
  localparam int unsigned X2_ADDR_BITS = $clog2(X2_LUT_DEPTH);
  localparam int unsigned X2_OUTPUTS_PER_ENTRY = X2_SCALE * X2_SCALE;
  localparam int unsigned X2_LUT_WORD_BITS =
      X2_OUTPUTS_PER_ENTRY * LUT_VALUE_BITS;

  localparam int unsigned X4_SCALE = 4;
  localparam int unsigned X4_SAMPLE_BITS = 5;
  localparam int unsigned X4_Q = 1 << X4_SAMPLE_BITS;
  localparam int unsigned X4_LEVELS = (1 << (PIXEL_BITS - X4_SAMPLE_BITS)) + 1;
  localparam int unsigned X4_LUT_DEPTH =
      X4_LEVELS * X4_LEVELS * X4_LEVELS * X4_LEVELS;
  localparam int unsigned X4_LOWER_BITS = $clog2(X4_LEVELS);
  localparam int unsigned X4_ADDR_BITS = $clog2(X4_LUT_DEPTH);
  localparam int unsigned X4_OUTPUTS_PER_ENTRY = X4_SCALE * X4_SCALE;
  localparam int unsigned X4_LUT_WORD_BITS =
      X4_OUTPUTS_PER_ENTRY * LUT_VALUE_BITS;

endpackage
