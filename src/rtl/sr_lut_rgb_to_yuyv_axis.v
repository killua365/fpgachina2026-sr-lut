`timescale 1ns/1ps

module sr_lut_rgb_to_yuyv_axis #(
  parameter integer OUTPUT_WIDTH = 1920,
  // Keep the derived counter width visible to Vivado's module-reference
  // parser instead of hiding it behind a localparam dependency.
  parameter integer COLUMN_BITS = (OUTPUT_WIDTH <= 2) ? 1 : $clog2(OUTPUT_WIDTH)
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 ACLK CLK" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ACLK, ASSOCIATED_BUSIF S_AXIS:M_AXIS, ASSOCIATED_RESET ARESETN, FREQ_HZ 100000000" *)
  input wire aclk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 ARESETN RST" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ARESETN, POLARITY ACTIVE_LOW" *)
  input wire aresetn,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1" *)
  input wire [31:0] s_axis_tdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *) input wire [3:0] s_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [15:0] m_axis_tdata,
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 2, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1, HAS_TUSER 1, TUSER_WIDTH 1" *)
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *) output wire [1:0] m_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TUSER" *) output wire m_axis_tuser,

  output reg frame_geometry_error
);

  reg [COLUMN_BITS-1:0] column_reg;
  reg frame_first_reg;

  wire generated_line_last = column_reg == OUTPUT_WIDTH - 1;
  wire input_fire = s_axis_tvalid && s_axis_tready;

  assign m_axis_tkeep = 2'b11;

  rgb_to_ycbcr converter (
    .clk(aclk),
    .rst_n(aresetn),
    // DMA words use little-endian RGBX bytes: [7:0]=R, [15:8]=G,
    // [23:16]=B. Reorder to the converter's packed {R,G,B} contract.
    .s_axis_tdata({s_axis_tdata[7:0], s_axis_tdata[15:8], s_axis_tdata[23:16]}),
    .s_axis_tvalid(s_axis_tvalid),
    .s_axis_tready(s_axis_tready),
    .s_axis_tlast(generated_line_last),
    .s_axis_tuser(frame_first_reg),
    .m_axis_tdata(m_axis_tdata),
    .m_axis_tvalid(m_axis_tvalid),
    .m_axis_tready(m_axis_tready),
    .m_axis_tlast(m_axis_tlast),
    .m_axis_tuser(m_axis_tuser)
  );

  always @(posedge aclk) begin
    if (!aresetn) begin
      column_reg <= 0;
      frame_first_reg <= 1'b1;
      frame_geometry_error <= 1'b0;
    end else if (input_fire) begin
      if (s_axis_tkeep != 4'b1111)
        frame_geometry_error <= 1'b1;
      if (s_axis_tlast && !generated_line_last)
        frame_geometry_error <= 1'b1;

      frame_first_reg <= s_axis_tlast;
      if (generated_line_last)
        column_reg <= 0;
      else
        column_reg <= column_reg + 1'b1;
    end
  end

endmodule
