`timescale 1ns/1ps

module sr_lut_row_bank_dynamic #(
  parameter int unsigned DEPTH = 4,
  parameter int unsigned WORD_BITS = 96,
  parameter int unsigned ADDR_BITS = (DEPTH <= 1) ? 1 : $clog2(DEPTH)
) (
  input  logic clk_i,
  input  logic write_enable_i,
  input  logic [ADDR_BITS - 1:0] write_address_i,
  input  logic [WORD_BITS - 1:0] write_data_i,
  input  logic read_enable_i,
  input  logic [ADDR_BITS - 1:0] read_address_i,
  output logic [WORD_BITS - 1:0] read_data_o
);

  (* ram_style = "block" *)
  logic [WORD_BITS - 1:0] memory [0:DEPTH - 1];

  always_ff @(posedge clk_i) begin
    if (write_enable_i) begin
      memory[write_address_i] <= write_data_i;
    end
  end

  always_ff @(posedge clk_i) begin
    if (read_enable_i) begin
      read_data_o <= memory[read_address_i];
    end
  end

endmodule

module sr_lut_output_fifo_dynamic #(
  parameter int unsigned SCALE = sr_lut_pkg::X2_SCALE,
  parameter int unsigned MAX_FRAME_WIDTH = 480,
  parameter int unsigned MAX_FRAME_HEIGHT = 320,
  parameter int unsigned PIXEL_BITS = sr_lut_pkg::PIXEL_BITS,
  parameter int unsigned CHANNELS = 3,
  parameter int unsigned BLOCK_ELEMENTS = SCALE * SCALE,
  parameter int unsigned RGB_BITS = PIXEL_BITS * CHANNELS
) (
  input  logic clk_i,
  input  logic resetn_i,
  input  logic [(BLOCK_ELEMENTS * PIXEL_BITS) - 1:0] s_block_i,
  input  logic [1:0] s_channel_i,
  input  logic s_valid_i,
  output logic s_ready_o,
  input  logic s_last_i,
  input  logic [9:0] active_width_i,
  input  logic [9:0] active_height_i,
  input  logic frame_start_i,
  output logic [RGB_BITS - 1:0] m_axis_tdata_o,
  output logic m_axis_tvalid_o,
  input  logic m_axis_tready_i,
  output logic m_axis_tlast_o
);

  localparam int unsigned BLOCK_BITS = BLOCK_ELEMENTS * PIXEL_BITS;
  localparam int unsigned ROW_WORD_BITS = CHANNELS * BLOCK_BITS;
  localparam int unsigned ROW_ADDR_BITS =
      (MAX_FRAME_WIDTH <= 1) ? 1 : $clog2(MAX_FRAME_WIDTH);

  logic [BLOCK_BITS - 1:0] pending_red;
  logic [BLOCK_BITS - 1:0] pending_green;
  logic [ROW_WORD_BITS - 1:0] bank_write_data;
  logic [ROW_WORD_BITS - 1:0] bank_read_data_0;
  logic [ROW_WORD_BITS - 1:0] bank_read_data_1;
  logic [ROW_WORD_BITS - 1:0] selected_read_data;
  logic [ROW_ADDR_BITS - 1:0] bank_write_address;
  logic [ROW_ADDR_BITS - 1:0] bank_read_address;
  logic [1:0] bank_write_enable;
  logic [1:0] bank_read_enable;

  logic [1:0] bank_full;
  logic [1:0] bank_last;
  logic write_bank;
  logic read_bank;
  logic reader_active;
  logic frame_active;

  integer input_row;
  integer input_column;
  logic [1:0] expected_channel;
  integer output_subrow;
  integer output_column;
  integer output_subcolumn;
  integer output_element;

  assign s_ready_o = frame_active && !bank_full[write_bank];
  assign m_axis_tvalid_o = frame_active && reader_active;
  assign m_axis_tlast_o = reader_active
      && bank_last[read_bank]
      && (output_subrow == SCALE - 1)
      && (output_column == active_width_i - 1)
      && (output_subcolumn == SCALE - 1);
  assign selected_read_data = read_bank
      ? bank_read_data_1 : bank_read_data_0;

  always_comb begin
    bank_write_enable = '0;
    bank_write_address = input_column[ROW_ADDR_BITS - 1:0];
    bank_write_data = {s_block_i, pending_green, pending_red};
    if (s_valid_i && s_ready_o && (s_channel_i == 2)) begin
      bank_write_enable[write_bank] = 1'b1;
    end
  end

  always_comb begin
    bank_read_enable = '0;
    bank_read_address = '0;

    if (!reader_active) begin
      if (bank_full[read_bank]) begin
        bank_read_enable[read_bank] = 1'b1;
      end
    end else if (m_axis_tvalid_o && m_axis_tready_i
                 && (output_subcolumn == SCALE - 1)) begin
      if (output_column < active_width_i - 1) begin
        bank_read_enable[read_bank] = 1'b1;
        bank_read_address = output_column + 1;
      end else if (output_subrow < SCALE - 1) begin
        bank_read_enable[read_bank] = 1'b1;
      end
    end
  end

  always_comb begin
    m_axis_tdata_o = '0;
    output_element = (output_subrow * SCALE) + output_subcolumn;

    if (reader_active) begin
      m_axis_tdata_o[(0 * PIXEL_BITS) +: PIXEL_BITS] =
          selected_read_data[(0 * BLOCK_BITS)
                             + (output_element * PIXEL_BITS)
                             +: PIXEL_BITS];
      m_axis_tdata_o[(1 * PIXEL_BITS) +: PIXEL_BITS] =
          selected_read_data[(1 * BLOCK_BITS)
                             + (output_element * PIXEL_BITS)
                             +: PIXEL_BITS];
      m_axis_tdata_o[(2 * PIXEL_BITS) +: PIXEL_BITS] =
          selected_read_data[(2 * BLOCK_BITS)
                             + (output_element * PIXEL_BITS)
                             +: PIXEL_BITS];
    end
  end

  sr_lut_row_bank_dynamic #(
    .DEPTH(MAX_FRAME_WIDTH),
    .WORD_BITS(ROW_WORD_BITS),
    .ADDR_BITS(ROW_ADDR_BITS)
  ) row_bank_0 (
    .clk_i(clk_i),
    .write_enable_i(bank_write_enable[0]),
    .write_address_i(bank_write_address),
    .write_data_i(bank_write_data),
    .read_enable_i(bank_read_enable[0]),
    .read_address_i(bank_read_address),
    .read_data_o(bank_read_data_0)
  );

  sr_lut_row_bank_dynamic #(
    .DEPTH(MAX_FRAME_WIDTH),
    .WORD_BITS(ROW_WORD_BITS),
    .ADDR_BITS(ROW_ADDR_BITS)
  ) row_bank_1 (
    .clk_i(clk_i),
    .write_enable_i(bank_write_enable[1]),
    .write_address_i(bank_write_address),
    .write_data_i(bank_write_data),
    .read_enable_i(bank_read_enable[1]),
    .read_address_i(bank_read_address),
    .read_data_o(bank_read_data_1)
  );

  always_ff @(posedge clk_i) begin
    if (!resetn_i) begin
      pending_red <= '0;
      pending_green <= '0;
      bank_full <= '0;
      bank_last <= '0;
      write_bank <= 1'b0;
      read_bank <= 1'b0;
      reader_active <= 1'b0;
      input_row <= 0;
      input_column <= 0;
      expected_channel <= 0;
      output_subrow <= 0;
      output_column <= 0;
      output_subcolumn <= 0;
      frame_active <= 1'b0;
    end else if (frame_start_i) begin
      pending_red <= '0;
      pending_green <= '0;
      bank_full <= '0;
      bank_last <= '0;
      write_bank <= 1'b0;
      read_bank <= 1'b0;
      reader_active <= 1'b0;
      input_row <= 0;
      input_column <= 0;
      expected_channel <= 0;
      output_subrow <= 0;
      output_column <= 0;
      output_subcolumn <= 0;
      frame_active <= 1'b1;
    end else begin
      if (s_valid_i && s_ready_o) begin
        case (s_channel_i)
          0: pending_red <= s_block_i;
          1: pending_green <= s_block_i;
          default: begin end
        endcase

        if (s_channel_i == 2) begin
          expected_channel <= 0;
          if (input_column == active_width_i - 1) begin
            bank_full[write_bank] <= 1'b1;
            bank_last[write_bank] <= s_last_i;
            write_bank <= !write_bank;
            input_column <= 0;
            if (s_last_i) begin
              input_row <= 0;
            end else begin
              input_row <= input_row + 1;
            end
          end else begin
            input_column <= input_column + 1;
          end
        end else begin
          expected_channel <= expected_channel + 1'b1;
        end
      end

      if (!reader_active) begin
        if (bank_full[read_bank]) begin
          reader_active <= 1'b1;
          output_subrow <= 0;
          output_column <= 0;
          output_subcolumn <= 0;
        end
      end else if (m_axis_tvalid_o && m_axis_tready_i) begin
        if (output_subcolumn == SCALE - 1) begin
          output_subcolumn <= 0;
          if (output_column == active_width_i - 1) begin
            output_column <= 0;
            if (output_subrow == SCALE - 1) begin
              output_subrow <= 0;
              reader_active <= 1'b0;
              bank_full[read_bank] <= 1'b0;
              read_bank <= !read_bank;
              if (bank_last[read_bank]) begin
                frame_active <= 1'b0;
              end
            end else begin
              output_subrow <= output_subrow + 1;
            end
          end else begin
            output_column <= output_column + 1;
          end
        end else begin
          output_subcolumn <= output_subcolumn + 1;
        end
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((SCALE != 2) && (SCALE != 4)) begin
      $fatal(1, "output reorder supports only scale 2 or 4");
    end
    if ((MAX_FRAME_WIDTH < 1) || (MAX_FRAME_HEIGHT < 1)) begin
      $fatal(1, "output reorder frame dimensions must be positive");
    end
    if (CHANNELS != 3) begin
      $fatal(1, "output reorder requires exactly three RGB channels");
    end
    if (BLOCK_ELEMENTS != (SCALE * SCALE)) begin
      $fatal(1, "BLOCK_ELEMENTS must equal SCALE * SCALE");
    end
    if (RGB_BITS != (PIXEL_BITS * CHANNELS)) begin
      $fatal(1, "RGB_BITS must equal PIXEL_BITS * CHANNELS");
    end
  end

  always_ff @(posedge clk_i) begin
    if (resetn_i && s_valid_i && s_ready_o) begin
      if (s_channel_i !== expected_channel) begin
        $fatal(
          1,
          "output reorder channel mismatch actual=%0d expected=%0d",
          s_channel_i, expected_channel
        );
      end
      if (s_last_i
          && !(input_row == active_height_i - 1
               && input_column == active_width_i - 1
               && s_channel_i == 2)) begin
        $fatal(1, "output reorder input last asserted too early");
      end
      if (!s_last_i
          && input_row == active_height_i - 1
          && input_column == active_width_i - 1
          && s_channel_i == 2) begin
        $fatal(1, "output reorder input last missing on final block");
      end
    end
  end
`endif

endmodule
