`timescale 1ns/1ps

module sr_lut_address_gen #(
  parameter int unsigned LOWER_BITS = sr_lut_pkg::X2_LOWER_BITS,
  parameter int unsigned LEVELS = sr_lut_pkg::X2_LEVELS,
  parameter int unsigned ADDR_BITS = sr_lut_pkg::X2_ADDR_BITS,
  parameter int unsigned LANES = sr_lut_pkg::NEIGHBOR_COUNT,
  parameter int unsigned MASK_BITS = sr_lut_pkg::SIMPLEX_MASK_BITS,
  parameter int unsigned VERTICES = sr_lut_pkg::SIMPLEX_VERTEX_COUNT
) (
  input  logic [(LANES * LOWER_BITS) - 1:0] lower_i,
  input  logic [(VERTICES * MASK_BITS) - 1:0] masks_i,
  output logic [(VERTICES * ADDR_BITS) - 1:0] addresses_o
);

  logic [MASK_BITS - 1:0] mask_value;
  logic [LOWER_BITS:0] coordinate_a;
  logic [LOWER_BITS:0] coordinate_b;
  logic [LOWER_BITS:0] coordinate_c;
  logic [LOWER_BITS:0] coordinate_d;
  logic [ADDR_BITS - 1:0] address_value;
  integer vertex;

  always_comb begin
    addresses_o = '0;
    mask_value = '0;
    coordinate_a = '0;
    coordinate_b = '0;
    coordinate_c = '0;
    coordinate_d = '0;
    address_value = '0;

    for (vertex = 0; vertex < VERTICES; vertex = vertex + 1) begin
      mask_value = masks_i[(vertex * MASK_BITS) +: MASK_BITS];

      // mask[3], [2], [1], [0] correspond to a, b, c, d respectively.
      coordinate_a = {1'b0, lower_i[(0 * LOWER_BITS) +: LOWER_BITS]}
                    + mask_value[3];
      coordinate_b = {1'b0, lower_i[(1 * LOWER_BITS) +: LOWER_BITS]}
                    + mask_value[2];
      coordinate_c = {1'b0, lower_i[(2 * LOWER_BITS) +: LOWER_BITS]}
                    + mask_value[1];
      coordinate_d = {1'b0, lower_i[(3 * LOWER_BITS) +: LOWER_BITS]}
                    + mask_value[0];

      address_value =
          (((coordinate_a * LEVELS) + coordinate_b) * LEVELS
           + coordinate_c) * LEVELS + coordinate_d;
      addresses_o[(vertex * ADDR_BITS) +: ADDR_BITS] = address_value;
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((LANES != 4) || (MASK_BITS != 4) || (VERTICES != 5)) begin
      $fatal(1, "Model-S address generation requires 4 lanes and 5 vertices");
    end
    if (LEVELS < 2) begin
      $fatal(1, "LEVELS must be at least 2");
    end
    if (LOWER_BITS != $clog2(LEVELS)) begin
      $fatal(1, "LOWER_BITS must equal $clog2(LEVELS)");
    end
    if (ADDR_BITS != $clog2(LEVELS * LEVELS * LEVELS * LEVELS)) begin
      $fatal(1, "ADDR_BITS does not cover the configured LUT depth");
    end
  end
`endif

endmodule
