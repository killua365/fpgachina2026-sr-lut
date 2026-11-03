// Pixel-domain validator for fixed positive-sync, progressive 1080p30 RGB video.
// There is deliberately no ready input: HDMI cannot be backpressured.
// Frame validity is decided at the NEXT VS edge, after all geometry is known.
module hdmi_geometry #(
    parameter integer WIDTH=1920,HEIGHT=1080,HTOTAL=2200,VTOTAL=1125,
    parameter integer HSYNC=44,VSYNC=5
)(
    input wire clk,resetn,locked,de,hs,vs,input wire [23:0] rbg,
    output wire pixel_valid,sof,eol,output wire [31:0] rgbx,
    output reg frame_end=0,frame_good=0,frame_bad=0,
    output reg [31:0] frames=0,good_frames=0,bad_frames=0,
    output reg [31:0] last_pixels=0,last_lines=0,last_hperiod=0,last_vperiod=0
);
    reg prev_de=0,prev_hs=0,prev_vs=0,tracking=0,bad=0,have_h=0;
    reg [31:0] x=0,y=0,pixels=0,hperiod=0,vperiod=0,hwidth=0,vwidth=0;
    wire hrise=hs&&!prev_hs,vrise=vs&&!prev_vs;
    wire dfall=!de&&prev_de;
    wire geometry_bad=(de&&(hs||vs||x>=WIDTH||y>=HEIGHT))||
        (dfall&&x!=WIDTH)||(hrise&&have_h&&hperiod+1!=HTOTAL)||
        (!hs&&prev_hs&&hwidth!=HSYNC)||(!vs&&prev_vs&&vwidth!=VSYNC*HTOTAL);
    assign pixel_valid=tracking&&locked&&de&&!vrise;
    assign sof=pixel_valid&&pixels==0;
    assign eol=pixel_valid&&x==WIDTH-1;
    assign rgbx={8'h00,rbg[15:8],rbg[7:0],rbg[23:16]};
    always @(posedge clk)begin
        frame_end<=0;frame_good<=0;frame_bad<=0;
        if(!resetn)begin
            prev_de<=0;prev_hs<=0;prev_vs<=0;tracking<=0;bad<=0;have_h<=0;
            x<=0;y<=0;pixels<=0;hperiod<=0;vperiod<=0;hwidth<=0;vwidth<=0;
            frames<=0;good_frames<=0;bad_frames<=0;
            last_pixels<=0;last_lines<=0;last_hperiod<=0;last_vperiod<=0;
        end else begin
            prev_de<=de;prev_hs<=hs;prev_vs<=vs;
            if(!locked)begin
                // No end token is promised if the source clock disappears.
                // The root-clock ingress watchdog must retire such a frame.
                tracking<=0;bad<=0;have_h<=0;x<=0;y<=0;pixels<=0;
                hperiod<=0;vperiod<=0;hwidth<=0;vwidth<=0;
            end else begin
                if(hperiod!=32'hffffffff)hperiod<=hperiod+1'b1;
                if(vperiod!=32'hffffffff)vperiod<=vperiod+1'b1;
                if(hs)begin if(hwidth!=32'hffffffff)hwidth<=hwidth+1'b1;end else hwidth<=0;
                if(vs)begin if(vwidth!=32'hffffffff)vwidth<=vwidth+1'b1;end else vwidth<=0;
                if(hrise)begin hperiod<=0;have_h<=1;last_hperiod<=hperiod+1'b1;end
                if(tracking&&geometry_bad)bad<=1;
                if(pixel_valid)begin
                    if(x!=32'hffffffff)x<=x+1'b1;
                    if(pixels!=32'hffffffff)pixels<=pixels+1'b1;
                end
                if(dfall)begin x<=0;if(y!=32'hffffffff)y<=y+1'b1;end
                if(vrise)begin
                    if(tracking)begin
                        frame_end<=1;frames<=frames+1'b1;
                        last_pixels<=pixels;last_lines<=y;last_vperiod<=vperiod+1'b1;
                        if(!bad&&!geometry_bad&&pixels==WIDTH*HEIGHT&&y==HEIGHT&&vperiod+1==HTOTAL*VTOTAL)begin
                            frame_good<=1;good_frames<=good_frames+1'b1;
                        end else begin frame_bad<=1;bad_frames<=bad_frames+1'b1;end
                    end
                    tracking<=1;bad<=0;x<=0;y<=0;pixels<=0;vperiod<=0;
                end
            end
        end
    end
endmodule
