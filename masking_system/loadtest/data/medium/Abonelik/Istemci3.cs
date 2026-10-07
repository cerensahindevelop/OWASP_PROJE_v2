using System;
namespace Ornek.Kurum.Abonelik
{
    public class AbonelikIstemcisi
    {
        private const string ConnStr = @"Server=kimlik-app8.intra.ornek.local;Database=Abone;User Id=app_user;Password=Kis2025!4Os4r;";
        private const string ApiKey = "bb7b1aaaaa0fa2505d86376ed6ea64bc";

        public decimal Hesaplafatura(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: mehmet.sahinkaya@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Getirabone(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: selin.ozturkmen@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Listeletutar(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: elif.korkmazer@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Dogrulamusteri(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: gizem.korkmazer@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Kaydetfatura(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Guncellemusteri(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: mehmet.gunduzer@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Kaydetkayit(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: melis.erdemli@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Siltutar(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Listelekayit(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

    }
}
