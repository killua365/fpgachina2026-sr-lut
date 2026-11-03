`timescale 1ns/1ps

module sr_lut_axis_async_fifo_16 #(
  parameter integer FIFO_DEPTH = 64
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 S_CLK CLK" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_CLK, ASSOCIATED_BUSIF S_AXIS, ASSOCIATED_RESET S_RESETN, FREQ_HZ 100000000" *)
  input wire s_clk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 S_RESETN RST" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_RESETN, POLARITY ACTIVE_LOW" *)
  input wire s_resetn,
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 M_CLK CLK" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_CLK, ASSOCIATED_BUSIF M_AXIS, ASSOCIATED_RESET M_RESETN, FREQ_HZ 150000000" *)
  input wire m_clk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 M_RESETN RST" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_RESETN, POLARITY ACTIVE_LOW" *)
  input wire m_resetn,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 2, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1, HAS_TUSER 1, TUSER_WIDTH 1" *)
  input wire [15:0] s_axis_tdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *) input wire [1:0] s_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TUSER" *) input wire s_axis_tuser,

  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [15:0] m_axis_tdata,
  (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 2, HAS_TKEEP 1, HAS_TLAST 1, HAS_TREADY 1, HAS_TUSER 1, TUSER_WIDTH 1" *)
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *) output wire [1:0] m_axis_tkeep,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TUSER" *) output wire m_axis_tuser
);

  wire [19:0] fifo_in = {s_axis_tuser, s_axis_tlast, s_axis_tkeep, s_axis_tdata};
  wire [19:0] fifo_out;
  wire fifo_full, fifo_empty, wr_rst_busy, rd_rst_busy;
  // XPM synchronizes reset release into both FIFO clock domains internally.
  wire fifo_reset = !s_resetn;

  assign s_axis_tready = !fifo_full && !wr_rst_busy;
  assign m_axis_tvalid = m_resetn && !fifo_empty && !rd_rst_busy;
  assign {m_axis_tuser, m_axis_tlast, m_axis_tkeep, m_axis_tdata} = fifo_out;

  xpm_fifo_async #(
    .CDC_SYNC_STAGES(2),
    .DOUT_RESET_VALUE("0"),
    .ECC_MODE("no_ecc"),
    .FIFO_MEMORY_TYPE("auto"),
    .FIFO_READ_LATENCY(0),
    .FIFO_WRITE_DEPTH(FIFO_DEPTH),
    .FULL_RESET_VALUE(0),
    .PROG_EMPTY_THRESH(10),
    .PROG_FULL_THRESH(10),
    .RD_DATA_COUNT_WIDTH($clog2(FIFO_DEPTH) + 1),
    .READ_DATA_WIDTH(20),
    .READ_MODE("fwft"),
    .RELATED_CLOCKS(0),
    .SIM_ASSERT_CHK(1),
    .USE_ADV_FEATURES("0000"),
    .WAKEUP_TIME(0),
    .WRITE_DATA_WIDTH(20),
    .WR_DATA_COUNT_WIDTH($clog2(FIFO_DEPTH) + 1)
  ) fifo (
    .rst(fifo_reset),
    .wr_clk(s_clk),
    .wr_en(s_axis_tvalid && s_axis_tready),
    .din(fifo_in),
    .full(fifo_full),
    .wr_rst_busy(wr_rst_busy),
    .rd_clk(m_clk),
    .rd_en(m_axis_tvalid && m_axis_tready),
    .dout(fifo_out),
    .empty(fifo_empty),
    .rd_rst_busy(rd_rst_busy),
    .sleep(1'b0),
    .injectsbiterr(1'b0),
    .injectdbiterr(1'b0)
  );

endmodule
